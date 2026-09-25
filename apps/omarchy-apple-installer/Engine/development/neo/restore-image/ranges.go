// SPDX-License-Identifier: MIT
// Extract catalog-pinned files through independently authenticated Apple ranges.
package main

import (
	"bytes"
	"compress/zlib"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hkdf"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/go-compressions/lzfse"
)

const segmentBytes = 1048576
const clusterWidth = 256
const clusterHeaderBytes = clusterWidth*72 + 32
const maxFileBytes = 64 * 1024 * 1024

type rangeRecord struct {
	Offset int64  `json:"offset"`
	Size   int64  `json:"size"`
	SHA256 string `json:"sha256"`
}
type clusterRecord struct {
	rangeRecord
	Cluster     int64  `json:"cluster"`
	ExpectedMAC string `json:"expected_mac"`
}
type imageRecord struct {
	Size   int64  `json:"size_bytes"`
	SHA256 string `json:"sha256"`
}
type sourceRecord struct {
	imageRecord
	URL string `json:"url"`
}
type systemRecord struct {
	imageRecord
	Member  string      `json:"member"`
	Decoded imageRecord `json:"decoded"`
}
type plainSpan struct {
	Offset int64 `json:"offset"`
	Size   int64 `json:"size"`
}
type storageRecord struct {
	Size   int64       `json:"size"`
	SHA256 string      `json:"sha256"`
	Spans  []plainSpan `json:"spans"`
}
type rangeFile struct {
	Source     string          `json:"source"`
	Name       string          `json:"name"`
	Transform  string          `json:"transform"`
	Compressed bool            `json:"compressed"`
	RawSHA256  string          `json:"raw_sha256"`
	SHA256     string          `json:"sha256"`
	Size       int64           `json:"size"`
	Storage    []storageRecord `json:"storage"`
}
type rangeRecipe struct {
	Schema       int                    `json:"schema"`
	Source       sourceRecord           `json:"source"`
	System       systemRecord           `json:"system_image"`
	MemberOffset int64                  `json:"member_offset"`
	ZIPHeader    rangeRecord            `json:"zip_header"`
	Prefix       rangeRecord            `json:"prefix"`
	Headers      []clusterRecord        `json:"headers"`
	Segments     map[string]rangeRecord `json:"segments"`
	Files        []rangeFile            `json:"files"`
}

// Only a failure after local recipe admission is eligible for the old,
// fully pinned extraction path. Cancellation is never a fallback signal.
type rangeFallbackError struct{ err error }
type rangeLocalError struct{ err error }

func (e *rangeLocalError) Error() string    { return e.err.Error() }
func (e *rangeLocalError) Unwrap() error    { return e.err }
func (e *rangeFallbackError) Error() string { return e.err.Error() }
func (e *rangeFallbackError) Unwrap() error { return e.err }

func validHash(value string) bool {
	data, err := hex.DecodeString(value)
	return err == nil && len(data) == 32 && hex.EncodeToString(data) == value
}
func hashMatches(data []byte, expected string) bool {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]) == expected
}
func safeRelative(name string) bool {
	return name != "" && name != "." && path.Clean(name) == name && !path.IsAbs(name) &&
		!strings.ContainsAny(name, "\\\x00\r\n") && name != ".." && !strings.HasPrefix(name, "../")
}
func appleRangeURL(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && u.Scheme == "https" && u.Host == "updates.cdn-apple.com" &&
		u.User == nil && u.RawQuery == "" && u.Fragment == "" && strings.HasPrefix(u.Path, "/")
}
func validExtent(r rangeRecord, limit, maximum int64) bool {
	return r.Offset >= 0 && r.Size > 0 && r.Size <= maximum && r.Offset <= limit-r.Size && validHash(r.SHA256)
}

func (r *rangeRecipe) validate() error {
	if r.Schema != 1 || !appleRangeURL(r.Source.URL) || r.Source.Size <= 0 || r.Source.Size > 128<<30 ||
		!validHash(r.Source.SHA256) || !validHash(r.System.SHA256) || !validHash(r.System.Decoded.SHA256) ||
		r.System.Size <= 0 || r.System.Size > r.Source.Size || r.System.Decoded.Size <= 0 || r.System.Decoded.Size > 128<<30 ||
		!safeRelative(r.System.Member) || r.MemberOffset < 0 || r.MemberOffset > r.Source.Size-r.System.Size ||
		!validExtent(r.ZIPHeader, r.Source.Size, 65536) || r.ZIPHeader.Offset+r.ZIPHeader.Size != r.MemberOffset ||
		!validExtent(r.Prefix, r.System.Size, 1048576+156) || r.Prefix.Offset != 0 || r.Prefix.Size < 156 ||
		len(r.Headers) < 1 || len(r.Headers) > 128 || len(r.Segments) < 1 || len(r.Segments) > 128 ||
		len(r.Files) < 1 || len(r.Files) > 128 {
		return errors.New("invalid firmware range recipe identity or resource bounds")
	}
	clusters := map[int64]bool{}
	var encryptedTotal int64
	for _, h := range r.Headers {
		if h.Cluster < 0 || h.Cluster >= (r.System.Decoded.Size+segmentBytes*clusterWidth-1)/(segmentBytes*clusterWidth) ||
			clusters[h.Cluster] || h.Size != clusterHeaderBytes || !validExtent(h.rangeRecord, r.System.Size, clusterHeaderBytes) ||
			h.Offset < r.Prefix.Size || !validHash(h.ExpectedMAC) {
			return errors.New("invalid or duplicate firmware cluster descriptor")
		}
		clusters[h.Cluster] = true
		encryptedTotal += h.Size
	}
	ids := map[int64]bool{}
	for key, s := range r.Segments {
		id, err := strconv.ParseInt(key, 10, 64)
		if err != nil || id < 0 || strconv.FormatInt(id, 10) != key || id >= (r.System.Decoded.Size+segmentBytes-1)/segmentBytes ||
			!clusters[id/clusterWidth] || !validExtent(s, r.System.Size, segmentBytes) {
			return errors.New("invalid firmware segment descriptor")
		}
		ids[id] = true
		encryptedTotal += s.Size
	}
	if encryptedTotal > maxFileBytes {
		return errors.New("firmware network budget exceeded")
	}
	names, needed, neededClusters := map[string]bool{}, map[int64]bool{}, map[int64]bool{}
	var outputTotal, storageTotal int64
	for _, file := range r.Files {
		if !safeRelative(file.Source) || !safeRelative(file.Name) || names[file.Name] ||
			!validHash(file.RawSHA256) || !validHash(file.SHA256) || file.Size < 0 || file.Size > maxFileBytes ||
			(file.Transform != "identity" && file.Transform != "nvram") ||
			len(file.Storage) < 1 || len(file.Storage) > 2 || (!file.Compressed && len(file.Storage) != 1) {
			return errors.New("invalid or duplicate output file descriptor")
		}
		for existing := range names {
			if strings.HasPrefix(file.Name, existing+"/") || strings.HasPrefix(existing, file.Name+"/") {
				return errors.New("conflicting output paths")
			}
		}
		names[file.Name] = true
		outputTotal += file.Size
		for _, storage := range file.Storage {
			if storage.Size < 0 || storage.Size > maxFileBytes || !validHash(storage.SHA256) || len(storage.Spans) > 4096 {
				return errors.New("invalid compressed storage descriptor")
			}
			storageTotal += storage.Size
			var size int64
			for _, span := range storage.Spans {
				if span.Offset < 0 || span.Size <= 0 || span.Size > storage.Size || span.Offset > r.System.Decoded.Size-span.Size {
					return errors.New("invalid firmware plaintext span")
				}
				size += span.Size
				if size > storage.Size {
					return errors.New("firmware storage span sum exceeded")
				}
				for id := span.Offset / segmentBytes; id <= (span.Offset+span.Size-1)/segmentBytes; id++ {
					if !ids[id] {
						return errors.New("firmware segment dependency is missing")
					}
					needed[id], neededClusters[id/clusterWidth] = true, true
				}
			}
			if size != storage.Size {
				return errors.New("firmware storage span sum mismatch")
			}
		}
	}
	if outputTotal > maxFileBytes || storageTotal > maxFileBytes || len(needed) != len(ids) || len(neededClusters) != len(clusters) {
		return errors.New("firmware output budget or dependency closure mismatch")
	}
	return nil
}

func readRecipe(name, expected string) (*rangeRecipe, error) {
	info, err := os.Lstat(name)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 1024*1024 || !validHash(expected) {
		return nil, errors.New("invalid firmware recipe file or digest")
	}
	data, err := os.ReadFile(name)
	if err != nil {
		return nil, err
	}
	if !hashMatches(data, expected) {
		return nil, errors.New("firmware recipe SHA-256 mismatch")
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var recipe rangeRecipe
	if err := decoder.Decode(&recipe); err != nil {
		return nil, err
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return nil, errors.New("trailing recipe data")
	}
	if err := recipe.validate(); err != nil {
		return nil, err
	}
	return &recipe, nil
}

func fetchRange(ctx context.Context, client *http.Client, source sourceRecord, record rangeRecord, base int64) ([]byte, error) {
	start := base + record.Offset
	end := start + record.Size - 1
	var last error
	for attempt := 0; attempt < 3; attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		data, err := func() ([]byte, error) {
			req, err := http.NewRequestWithContext(ctx, http.MethodGet, source.URL, nil)
			if err != nil {
				return nil, err
			}
			req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", start, end))
			req.Header.Set("Accept-Encoding", "identity")
			response, err := client.Do(req)
			if err != nil {
				return nil, err
			}
			defer response.Body.Close()
			if response.StatusCode != http.StatusPartialContent ||
				response.Header.Get("Content-Range") != fmt.Sprintf("bytes %d-%d/%d", start, end, source.Size) ||
				response.ContentLength != record.Size ||
				(response.Header.Get("Content-Encoding") != "" && response.Header.Get("Content-Encoding") != "identity") {
				return nil, errors.New("Apple did not return the exact requested firmware range")
			}
			data, err := io.ReadAll(io.LimitReader(response.Body, record.Size+1))
			if err != nil {
				return nil, err
			}
			if int64(len(data)) != record.Size || !hashMatches(data, record.SHA256) {
				return nil, errors.New("Apple firmware range size or SHA-256 mismatch")
			}
			return data, nil
		}()
		if err == nil {
			return data, nil
		}
		last = err
		if attempt < 2 {
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-time.After(time.Duration(attempt+1) * 250 * time.Millisecond):
			}
		}
	}
	return nil, last
}

func deriveRange(key, salt, info []byte, size int) ([]byte, error) {
	return hkdf.Key(sha256.New, key, salt, string(info), size)
}
func indexedInfo(name string, index int64) []byte {
	return binary.LittleEndian.AppendUint32([]byte(name), uint32(index))
}
func rangeMAC(material, data, salt, expected []byte) error {
	mac := hmac.New(sha256.New, material[:32])
	mac.Write(salt)
	mac.Write(data)
	mac.Write(binary.LittleEndian.AppendUint64(nil, uint64(len(salt))))
	if !hmac.Equal(mac.Sum(nil), expected) {
		return errors.New("AEA range authentication failed")
	}
	return nil
}
func rangeCTR(material, data []byte) ([]byte, error) {
	block, err := aes.NewCipher(material[32:64])
	if err != nil {
		return nil, err
	}
	result := make([]byte, len(data))
	cipher.NewCTR(block, material[64:80]).XORKeyStream(result, data)
	return result, nil
}

// Check every LZFSE block's declared output and payload bounds before entering
// the codec. This bounds allocation even for malformed authenticated inputs.
func boundedLZFSE(data []byte, maximum int64) ([]byte, error) {
	var total, maximumExpansion int64
	for offset := 0; ; {
		if offset+4 > len(data) {
			return nil, errors.New("truncated LZFSE stream")
		}
		magic := string(data[offset : offset+4])
		if magic == "bvx$" {
			if offset+4 != len(data) || total > maximum {
				return nil, errors.New("invalid LZFSE termination")
			}
			result, err := lzfse.Decompress(data)
			if err != nil {
				return nil, err
			}
			if int64(len(result)) != total {
				return nil, errors.New("LZFSE output length mismatch")
			}
			return result, nil
		}
		if offset+8 > len(data) {
			return nil, errors.New("truncated LZFSE header")
		}
		raw := int64(binary.LittleEndian.Uint32(data[offset+4:]))
		total += raw
		if raw > maximum || total > maximum {
			return nil, errors.New("LZFSE output budget exceeded")
		}
		var header, payload, matches int64
		switch magic {
		case "bvx-":
			header, payload = 8, raw
		case "bvxn":
			if offset+12 > len(data) {
				return nil, errors.New("truncated LZVN header")
			}
			header, payload = 12, int64(binary.LittleEndian.Uint32(data[offset+8:]))
		case "bvx1":
			if offset+772 > len(data) {
				return nil, errors.New("truncated LZFSE V1 header")
			}
			if binary.LittleEndian.Uint32(data[offset+12:]) > segmentBytes+4 {
				return nil, errors.New("LZFSE literal budget exceeded")
			}
			matches = int64(binary.LittleEndian.Uint32(data[offset+16:]))
			header = 772
			payload = int64(binary.LittleEndian.Uint32(data[offset+20:])) + int64(binary.LittleEndian.Uint32(data[offset+24:]))
		case "bvx2":
			if offset+32 > len(data) {
				return nil, errors.New("truncated LZFSE V2 header")
			}
			v0, v1 := binary.LittleEndian.Uint64(data[offset+8:]), binary.LittleEndian.Uint64(data[offset+16:])
			matches = int64((v0 >> 40) & 0xfffff)
			header = int64(binary.LittleEndian.Uint32(data[offset+24:]))
			payload = int64((v0>>20)&0xfffff) + int64((v1>>40)&0xfffff)
			if header < 32 {
				return nil, errors.New("invalid LZFSE V2 header length")
			}
		default:
			return nil, errors.New("unsupported LZFSE block")
		}
		// The decoder tolerates expansion beyond n_raw_bytes before rejecting it.
		// Bound the worst possible literal/match expansion as well as declarations.
		maximumExpansion += raw + matches*(315+2359)
		if maximumExpansion > 512*1024*1024 {
			return nil, errors.New("LZFSE match expansion budget exceeded")
		}
		if header+payload > int64(len(data)-offset) {
			return nil, errors.New("LZFSE block exceeds input")
		}
		offset += int(header + payload)
	}
}

type decodedSegment struct {
	Offset, Size, PlainSize int64
	Hash                    string
	MAC                     []byte
	Cluster, Index          int64
}

func decodeRanges(ctx context.Context, recipe *rangeRecipe, fetched map[rangeRecord][]byte) (map[int64][]byte, error) {
	prefix := fetched[recipe.Prefix]
	if len(prefix) < 156 || string(prefix[:4]) != "AEA1" || binary.LittleEndian.Uint32(prefix[4:8]) != 1 {
		return nil, errors.New("unsupported AEA range profile")
	}
	authLength := int(binary.LittleEndian.Uint32(prefix[8:12]))
	if authLength > maxMetadata || len(prefix) != 12+authLength+32+112 {
		return nil, errors.New("invalid AEA prefix length")
	}
	md, err := metadata(bytes.NewReader(prefix))
	if err != nil {
		return nil, err
	}
	publicKey, err := fetchKey(ctx, string(md["com.apple.wkms.fcs-key-url"]))
	if err != nil {
		return nil, err
	}
	key, err := unwrap(md, publicKey)
	if err != nil {
		return nil, err
	}
	auth, salt := prefix[12:12+authLength], prefix[12+authLength:44+authLength]
	main, err := deriveRange(key, salt, append([]byte("AEA_AMK"), prefix[4:8]...), 32)
	if err != nil {
		return nil, err
	}
	root := prefix[44+authLength:]
	material, err := deriveRange(main, nil, []byte("AEA_RHEK"), 80)
	if err != nil {
		return nil, err
	}
	if err := rangeMAC(material, root[32:80], append(append([]byte{}, root[80:]...), auth...), root[:32]); err != nil {
		return nil, err
	}
	plain, err := rangeCTR(material, root[32:80])
	if err != nil {
		return nil, err
	}
	if binary.LittleEndian.Uint64(plain[:8]) != uint64(recipe.System.Decoded.Size) ||
		binary.LittleEndian.Uint64(plain[8:16]) != uint64(recipe.System.Size) ||
		binary.LittleEndian.Uint32(plain[16:20]) != segmentBytes || binary.LittleEndian.Uint32(plain[20:24]) != clusterWidth ||
		plain[24] != 'e' || plain[25] != 2 {
		return nil, errors.New("AEA range image parameters differ from recipe")
	}
	descriptions := map[int64]decodedSegment{}
	for _, h := range recipe.Headers {
		data := fetched[h.rangeRecord]
		clusterKey, err := deriveRange(main, nil, indexedInfo("AEA_CK", h.Cluster), 32)
		if err != nil {
			return nil, err
		}
		material, err := deriveRange(clusterKey, nil, []byte("AEA_CHEK"), 80)
		if err != nil {
			return nil, err
		}
		expected, _ := hex.DecodeString(h.ExpectedMAC)
		if h.Cluster == 0 && (!hmac.Equal(expected, root[80:]) || h.Offset != recipe.Prefix.Size) {
			return nil, errors.New("AEA first cluster differs from root")
		}
		split := clusterWidth * 40
		if err := rangeMAC(material, data[:split], data[split:], expected); err != nil {
			return nil, err
		}
		headers, err := rangeCTR(material, data[:split])
		if err != nil {
			return nil, err
		}
		offset := h.Offset + h.Size
		for index := int64(0); index < clusterWidth; index++ {
			id := h.Cluster*clusterWidth + index
			entry := headers[index*40 : (index+1)*40]
			plainSize, size := int64(binary.LittleEndian.Uint32(entry[:4])), int64(binary.LittleEndian.Uint32(entry[4:8]))
			expectedSize := min(int64(segmentBytes), max(int64(0), recipe.System.Decoded.Size-id*segmentBytes))
			if plainSize != expectedSize || size < 0 || size > plainSize || (plainSize > 0 && size == 0) || offset > recipe.System.Size-size {
				return nil, errors.New("invalid AEA segment bounds")
			}
			if _, wanted := recipe.Segments[strconv.FormatInt(id, 10)]; wanted {
				start := split + 32 + int(index)*32
				descriptions[id] = decodedSegment{offset, size, plainSize, hex.EncodeToString(entry[8:40]), data[start : start+32], h.Cluster, index}
			}
			offset += size
		}
	}
	decoded := map[int64][]byte{}
	for key, record := range recipe.Segments {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		id, _ := strconv.ParseInt(key, 10, 64)
		s, exists := descriptions[id]
		if !exists || s.Offset != record.Offset || s.Size != record.Size {
			return nil, errors.New("AEA segment differs from recipe")
		}
		clusterKey, err := deriveRange(main, nil, indexedInfo("AEA_CK", s.Cluster), 32)
		if err != nil {
			return nil, err
		}
		material, err := deriveRange(clusterKey, nil, indexedInfo("AEA_SK", s.Index), 80)
		if err != nil {
			return nil, err
		}
		data := fetched[record]
		if err := rangeMAC(material, data, nil, s.MAC); err != nil {
			return nil, err
		}
		plain, err := rangeCTR(material, data)
		if err != nil {
			return nil, err
		}
		if s.Size != s.PlainSize {
			plain, err = boundedLZFSE(plain, s.PlainSize)
			if err != nil {
				return nil, err
			}
		}
		if int64(len(plain)) != s.PlainSize || !hashMatches(plain, s.Hash) {
			return nil, errors.New("AEA plaintext segment SHA-256 mismatch")
		}
		decoded[id] = plain
	}
	return decoded, nil
}

// Decode the decmpfs storage envelope without opening an APFS filesystem.
// The recipe supplies authenticated attribute/resource-fork bytes; all table
// and decompression allocation bounds derive from the admitted output length.
func decompressStorage(storage [][]byte) ([]byte, error) {
	if len(storage) < 1 || len(storage) > 2 {
		return nil, errors.New("invalid APFS storage count")
	}
	attr := storage[0]
	if len(attr) < 16 || string(attr[:4]) != "fpmc" {
		return nil, errors.New("invalid APFS compression header")
	}
	kind := binary.LittleEndian.Uint32(attr[4:8])
	size := binary.LittleEndian.Uint64(attr[8:16])
	if size == 0 || size > maxFileBytes {
		return nil, errors.New("APFS output length exceeds budget")
	}
	if kind != 3 && kind != 4 && kind != 7 && kind != 8 && kind != 11 && kind != 12 {
		return nil, errors.New("unsupported APFS compression")
	}
	var blocks [][]byte
	if kind%2 == 1 {
		if len(storage) != 1 || size > 65536 || len(attr) <= 16 || len(attr) > 65553 {
			return nil, errors.New("invalid inline APFS storage")
		}
		blocks = append(blocks, attr[16:])
	} else {
		if len(storage) != 2 || len(attr) != 16 {
			return nil, errors.New("invalid APFS resource-fork storage")
		}
		fork := storage[1]
		count := int((size + 65535) / 65536)
		if kind == 4 {
			if len(fork) < 264+count*8 || binary.BigEndian.Uint32(fork[:4]) != 256 || int(binary.LittleEndian.Uint32(fork[260:264])) != count {
				return nil, errors.New("invalid zlib resource table")
			}
			previous := 264 + count*8
			for i := 0; i < count; i++ {
				start := int64(binary.LittleEndian.Uint32(fork[264+i*8:])) + 260
				length := int64(binary.LittleEndian.Uint32(fork[268+i*8:]))
				if start < int64(previous) || length <= 0 || length > 65537 || start > int64(len(fork))-length {
					return nil, errors.New("invalid zlib resource extent")
				}
				blocks = append(blocks, fork[start:start+length])
				previous = int(start + length)
			}
		} else {
			table := (count + 1) * 4
			if len(fork) < table || int(binary.LittleEndian.Uint32(fork[:4])) != table {
				return nil, errors.New("invalid APFS resource table length")
			}
			for i := 0; i < count; i++ {
				start, end := int64(binary.LittleEndian.Uint32(fork[i*4:])), int64(binary.LittleEndian.Uint32(fork[(i+1)*4:]))
				if start < int64(table) || end <= start || end-start > 65537 || end > int64(len(fork)) {
					return nil, errors.New("invalid APFS resource extent")
				}
				blocks = append(blocks, fork[start:end])
				if i == count-1 && end != int64(len(fork)) {
					return nil, errors.New("unexpected APFS resource tail")
				}
			}
		}
	}
	result := make([]byte, 0, size)
	for _, block := range blocks {
		expected := min(65536, int(size)-len(result))
		var plain []byte
		var err error
		switch kind {
		case 3, 4:
			if block[0] == 0xff {
				plain = block[1:]
			} else {
				reader, openErr := zlib.NewReader(bytes.NewReader(block))
				if openErr != nil {
					return nil, openErr
				}
				plain, err = io.ReadAll(io.LimitReader(reader, int64(expected)+1))
				reader.Close()
			}
		case 7, 8:
			if block[0] == 6 {
				plain = block[1:]
			} else {
				plain, err = lzfse.DecompressLZVN(block, expected)
			}
		case 11, 12:
			if block[0] != 'b' {
				plain = block[1:]
			} else {
				plain, err = boundedLZFSE(block, int64(expected))
			}
		}
		if err != nil {
			return nil, err
		}
		if len(plain) != expected {
			return nil, errors.New("APFS block output length mismatch")
		}
		result = append(result, plain...)
	}
	if uint64(len(result)) != size {
		return nil, errors.New("APFS file length mismatch")
	}
	return result, nil
}

func assembleRangeFiles(ctx context.Context, recipe *rangeRecipe, decoded map[int64][]byte, output string) error {
	if _, err := os.Lstat(output); !errors.Is(err, os.ErrNotExist) {
		return errors.New("firmware output already exists")
	}
	work, err := os.MkdirTemp(filepath.Dir(output), ".firmware-ranges-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(work)
	for _, file := range recipe.Files {
		if err := ctx.Err(); err != nil {
			return err
		}
		var storage [][]byte
		for _, record := range file.Storage {
			data := make([]byte, 0, record.Size)
			for _, span := range record.Spans {
				offset, remaining := span.Offset, span.Size
				for remaining > 0 {
					segment, exists := decoded[offset/segmentBytes]
					within := offset % segmentBytes
					if !exists || within >= int64(len(segment)) {
						return errors.New("missing plaintext dependency")
					}
					count := min(remaining, int64(len(segment))-within)
					data = append(data, segment[within:within+count]...)
					offset += count
					remaining -= count
				}
			}
			if int64(len(data)) != record.Size || !hashMatches(data, record.SHA256) {
				return errors.New("APFS storage SHA-256 mismatch")
			}
			storage = append(storage, data)
		}
		result := storage[0]
		if file.Compressed {
			result, err = decompressStorage(storage)
			if err != nil {
				return err
			}
		}
		if !hashMatches(result, file.RawSHA256) {
			return errors.New("source firmware SHA-256 mismatch")
		}
		if file.Transform == "nvram" {
			var normalized strings.Builder
			for _, b := range result {
				if b > 127 {
					return errors.New("non-ASCII NVRAM")
				}
			}
			for _, line := range strings.Split(string(result), "\n") {
				if line == "" {
					continue
				}
				key, value, ok := strings.Cut(line, "=")
				if !ok {
					return errors.New("invalid NVRAM record")
				}
				normalized.WriteString(strings.Trim(key, " \t\r\v\f"))
				normalized.WriteByte('=')
				normalized.WriteString(value)
				normalized.WriteByte('\n')
			}
			result = []byte(normalized.String())
		}
		if int64(len(result)) != file.Size || !hashMatches(result, file.SHA256) {
			return errors.New("output firmware SHA-256 mismatch")
		}
		target := filepath.Join(work, file.Name)
		if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
			return err
		}
		if err := os.WriteFile(target, result, 0600); err != nil {
			return err
		}
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if _, err := os.Lstat(output); !errors.Is(err, os.ErrNotExist) {
		return errors.New("firmware output appeared during extraction")
	}
	return os.Rename(work, output)
}

func extractFirmwareRanges(ctx context.Context, recipePath, expected, output string) error {
	recipe, err := readRecipe(recipePath, expected)
	if err != nil {
		return err
	}
	if _, err := os.Lstat(output); !errors.Is(err, os.ErrNotExist) {
		return errors.New("firmware output already exists")
	}
	start := time.Now()
	client := &http.Client{Timeout: 45 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return errors.New("firmware range redirects are not admitted")
		}}
	run := func() error {
		zipHeader, err := fetchRange(ctx, client, recipe.Source, recipe.ZIPHeader, 0)
		if err != nil {
			return err
		}
		if len(zipHeader) < 30 || string(zipHeader[:4]) != "PK\x03\x04" || binary.LittleEndian.Uint16(zipHeader[8:10]) != 0 {
			return errors.New("system image is not a stored ZIP member")
		}
		nameSize, extraSize := int(binary.LittleEndian.Uint16(zipHeader[26:28])), int(binary.LittleEndian.Uint16(zipHeader[28:30]))
		if len(zipHeader) != 30+nameSize+extraSize || string(zipHeader[30:30+nameSize]) != recipe.System.Member {
			return errors.New("ZIP local header identity mismatch")
		}
		records := []rangeRecord{recipe.Prefix}
		for _, h := range recipe.Headers {
			records = append(records, h.rangeRecord)
		}
		keys := make([]string, 0, len(recipe.Segments))
		for key := range recipe.Segments {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			records = append(records, recipe.Segments[key])
		}
		total := recipe.ZIPHeader.Size
		for _, record := range records {
			total += record.Size
		}
		fmt.Fprintf(os.Stderr, "Apple inputs: Downloading %.2f MB of authenticated firmware ranges\n", float64(total)/1e6)
		fetched := map[rangeRecord][]byte{}
		var mutex sync.Mutex
		var group sync.WaitGroup
		var firstError error
		jobs := make(chan rangeRecord)
		for worker := 0; worker < 6; worker++ {
			group.Add(1)
			go func() {
				defer group.Done()
				for record := range jobs {
					data, err := fetchRange(ctx, client, recipe.Source, record, recipe.MemberOffset)
					mutex.Lock()
					if err != nil {
						if firstError == nil {
							firstError = err
						}
					} else {
						fetched[record] = data
					}
					mutex.Unlock()
				}
			}()
		}
		for _, record := range records {
			jobs <- record
		}
		close(jobs)
		group.Wait()
		if firstError != nil {
			return firstError
		}
		decoded, err := decodeRanges(ctx, recipe, fetched)
		if err != nil {
			return err
		}
		if err := assembleRangeFiles(ctx, recipe, decoded, output); err != nil {
			return &rangeLocalError{err}
		}
		fmt.Fprintf(os.Stderr, "Apple inputs: Verified %d firmware files using %.2f MB in %.2f seconds\n", len(recipe.Files), float64(total)/1e6, time.Since(start).Seconds())
		return nil
	}
	if err := run(); err != nil {
		var local *rangeLocalError
		if errors.As(err, &local) {
			return err
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return err
		}
		return &rangeFallbackError{err}
	}
	return nil
}
