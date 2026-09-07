// SPDX-License-Identifier: MIT
package main

import (
	"bytes"
	"compress/zlib"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/go-compressions/lzfse"
)

func digest(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }
func productionRecipe(t *testing.T) *rangeRecipe {
	t.Helper()
	data, err := os.ReadFile("../profiles/j713-25G83-ranges.json")
	if err != nil {
		t.Fatal(err)
	}
	var recipe rangeRecipe
	if err := json.Unmarshal(data, &recipe); err != nil {
		t.Fatal(err)
	}
	if err := recipe.validate(); err != nil {
		t.Fatal(err)
	}
	return &recipe
}
func TestRangeRecipeAdmission(t *testing.T) {
	for name, mutate := range map[string]func(*rangeRecipe){
		"escape":    func(r *rangeRecipe) { r.Files[0].Name = "../escape" },
		"duplicate": func(r *rangeRecipe) { r.Files = append(r.Files, r.Files[0]) },
		"source":    func(r *rangeRecipe) { r.Source.URL = "https://example.org/archive" },
		"missing segment": func(r *rangeRecipe) {
			for k := range r.Segments {
				delete(r.Segments, k)
				break
			}
		},
		"oversize": func(r *rangeRecipe) { r.Files[0].Size = maxFileBytes + 1 },
		"span":     func(r *rangeRecipe) { r.Files[0].Storage[0].Spans[0].Offset = r.System.Decoded.Size },
	} {
		t.Run(name, func(t *testing.T) {
			r := productionRecipe(t)
			mutate(r)
			if r.validate() == nil {
				t.Fatal("invalid recipe admitted")
			}
		})
	}
	data, _ := os.ReadFile("../profiles/j713-25G83-ranges.json")
	p := filepath.Join(t.TempDir(), "recipe.json")
	os.WriteFile(p, data, 0600)
	if _, err := readRecipe(p, digest(data)); err != nil {
		t.Fatal(err)
	}
	if _, err := readRecipe(p, digest([]byte("changed"))); err == nil {
		t.Fatal("wrong digest admitted")
	}
}
func TestExactRangeTransport(t *testing.T) {
	body := []byte("test")
	for _, variant := range []string{"valid", "200", "extent", "length", "encoding", "changed"} {
		t.Run(variant, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("Range") != "bytes=7-10" || r.Header.Get("Accept-Encoding") != "identity" {
					t.Error("wrong request")
				}
				w.Header().Set("Content-Range", "bytes 7-10/20")
				w.Header().Set("Content-Length", "4")
				data := body
				switch variant {
				case "extent":
					w.Header().Set("Content-Range", "bytes 0-3/20")
				case "length":
					w.Header().Set("Content-Length", "5")
					data = []byte("extra")
				case "encoding":
					w.Header().Set("Content-Encoding", "gzip")
				case "changed":
					data = []byte("fail")
				}
				status := 206
				if variant == "200" {
					status = 200
				}
				w.WriteHeader(status)
				w.Write(data)
			}))
			defer server.Close()
			source := sourceRecord{URL: server.URL, imageRecord: imageRecord{Size: 20}}
			out, err := fetchRange(context.Background(), server.Client(), source, rangeRecord{Offset: 2, Size: 4, SHA256: digest(body)}, 5)
			if variant == "valid" {
				if err != nil || !bytes.Equal(out, body) {
					t.Fatal(out, err)
				}
			} else if err == nil {
				t.Fatal("invalid response admitted")
			}
			ctx, cancel := context.WithCancel(context.Background())
			cancel()
			if _, err := fetchRange(ctx, server.Client(), source, rangeRecord{}, 0); !errors.Is(err, context.Canceled) {
				t.Fatal(err)
			}
		})
	}
}
func TestRangeMACAuthenticatesDataAndSalt(t *testing.T) {
	material := bytes.Repeat([]byte{42}, 80)
	data, salt := []byte("ciphertext"), []byte("metadata")
	mac := hmac.New(sha256.New, material[:32])
	mac.Write(salt)
	mac.Write(data)
	mac.Write(binary.LittleEndian.AppendUint64(nil, uint64(len(salt))))
	expected := mac.Sum(nil)
	if err := rangeMAC(material, data, salt, expected); err != nil {
		t.Fatal(err)
	}
	if rangeMAC(material, []byte("changed"), salt, expected) == nil || rangeMAC(material, data, nil, expected) == nil {
		t.Fatal("unauthenticated data accepted")
	}
}
func TestBoundedCompression(t *testing.T) {
	plain := bytes.Repeat([]byte("synthetic compression fixture "), 200)
	compressed, err := lzfse.Compress(plain)
	if err != nil {
		t.Fatal(err)
	}
	if out, err := boundedLZFSE(compressed, int64(len(plain))); err != nil || !bytes.Equal(out, plain) {
		t.Fatal(err)
	}
	if _, err := boundedLZFSE(compressed, 1); err == nil {
		t.Fatal("output budget ignored")
	}
	if _, err := boundedLZFSE(compressed[:len(compressed)-1], int64(len(plain))); err == nil {
		t.Fatal("truncation admitted")
	}
	for _, kind := range []uint32{3, 4, 7, 8, 11, 12} {
		t.Run(fmt.Sprint(kind), func(t *testing.T) {
			attr := append([]byte("fpmc"), make([]byte, 12)...)
			binary.LittleEndian.PutUint32(attr[4:], kind)
			binary.LittleEndian.PutUint64(attr[8:], uint64(len(plain)))
			var block []byte
			switch kind {
			case 3, 4:
				var b bytes.Buffer
				w := zlib.NewWriter(&b)
				w.Write(plain)
				w.Close()
				block = b.Bytes()
			case 7, 8:
				block = lzfse.CompressLZVN(plain)
			case 11, 12:
				block = compressed
			}
			var storage [][]byte
			if kind%2 == 1 {
				storage = [][]byte{append(attr, block...)}
			} else if kind == 4 {
				fork := make([]byte, 272)
				binary.BigEndian.PutUint32(fork, 256)
				binary.LittleEndian.PutUint32(fork[260:], 1)
				binary.LittleEndian.PutUint32(fork[264:], 12)
				binary.LittleEndian.PutUint32(fork[268:], uint32(len(block)))
				storage = [][]byte{attr, append(fork, block...)}
			} else {
				fork := make([]byte, 8)
				binary.LittleEndian.PutUint32(fork, 8)
				binary.LittleEndian.PutUint32(fork[4:], uint32(8+len(block)))
				storage = [][]byte{attr, append(fork, block...)}
			}
			out, err := decompressStorage(storage)
			if err != nil || !bytes.Equal(out, plain) {
				t.Fatal(err)
			}
			binary.LittleEndian.PutUint64(storage[0][8:], maxFileBytes+1)
			if _, err := decompressStorage(storage); err == nil {
				t.Fatal("oversized APFS file admitted")
			}
		})
	}
	if _, err := decompressStorage(nil); err == nil {
		t.Fatal("empty storage admitted")
	}
}
func TestRangeFilesPublishOnlyAfterAllHashes(t *testing.T) {
	data := []byte("synthetic firmware")
	file := rangeFile{Name: "brcm/test.bin", Transform: "identity", Size: int64(len(data)), RawSHA256: digest(data), SHA256: digest(data), Storage: []storageRecord{{Size: int64(len(data)), SHA256: digest(data), Spans: []plainSpan{{Size: int64(len(data))}}}}}
	recipe := &rangeRecipe{Files: []rangeFile{file}}
	directory := t.TempDir()
	out := filepath.Join(directory, "firmware")
	recipe.Files[0].SHA256 = digest([]byte("different"))
	if assembleRangeFiles(context.Background(), recipe, map[int64][]byte{0: data}, out) == nil {
		t.Fatal("bad output admitted")
	}
	entries, _ := os.ReadDir(directory)
	if len(entries) != 0 {
		t.Fatal("incomplete files retained")
	}
	recipe.Files[0].SHA256 = digest(data)
	if err := assembleRangeFiles(context.Background(), recipe, map[int64][]byte{0: data}, out); err != nil {
		t.Fatal(err)
	}
	if err := assembleRangeFiles(context.Background(), recipe, map[int64][]byte{0: data}, out); err == nil {
		t.Fatal("existing output replaced")
	}
}
