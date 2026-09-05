// SPDX-License-Identifier: MIT
// Decode an admitted Apple Recovery image using Apple's native AEA utility.
package main

import (
  "bytes"
  "context"
  "crypto/ecdh"
  "crypto/ecdsa"
  "crypto/hpke"
  "crypto/sha256"
  "crypto/x509"
  "encoding/base64"
  "encoding/binary"
  "encoding/hex"
  "encoding/json"
  "encoding/pem"
  "errors"
  "fmt"
  "io"
  "net/http"
  "net/url"
  "os"
  "os/exec"
  "path/filepath"
  "regexp"
  "runtime"
  "strconv"
  "time"
)

const maxMetadata = 1024 * 1024

func metadata(r io.Reader) (map[string][]byte, error) {
  var header [12]byte
  if _, err := io.ReadFull(r, header[:]); err != nil {
    return nil, err
  }
  if string(header[:4]) != "AEA1" || binary.LittleEndian.Uint32(header[4:8]) != 1 {
    return nil, errors.New("unsupported AEA header/profile")
  }
  length := binary.LittleEndian.Uint32(header[8:])
  if length == 0 || length > maxMetadata {
    return nil, errors.New("invalid AEA metadata length")
  }
  data := make([]byte, length)
  if _, err := io.ReadFull(r, data); err != nil {
    return nil, err
  }
  result := make(map[string][]byte)
  for len(data) > 0 {
    if len(data) < 4 {
      return nil, errors.New("truncated AEA metadata entry")
    }
    size := binary.LittleEndian.Uint32(data[:4])
    if size < 6 || uint64(size) > uint64(len(data)) {
      return nil, errors.New("invalid AEA metadata entry size")
    }
    key, value, found := bytes.Cut(data[4:size], []byte{0})
    if !found || len(key) == 0 {
      return nil, errors.New("invalid AEA metadata key")
    }
    if _, exists := result[string(key)]; exists {
      return nil, errors.New("duplicate AEA metadata key")
    }
    result[string(key)] = value
    data = data[size:]
  }
  return result, nil
}

func keyURL(raw string) (*url.URL, error) {
  u, err := url.Parse(raw)
  if err != nil {
    return nil, err
  }
  appleHost := u.Host == "wkms-public.apple.com" || u.Host == "fcs-keys-pub-prod.cdn-apple.com"
  if u.Scheme != "https" || !appleHost || u.User != nil ||
    u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" ||
    !regexp.MustCompile(`^/fcs-keys/[A-Za-z0-9_-]{43}=$`).MatchString(u.Path) {
    return nil, errors.New("AEA public key URL is outside Apple's FCS service")
  }
  return u, nil
}

func fetchKey(ctx context.Context, raw string) ([]byte, error) {
  u, err := keyURL(raw)
  if err != nil {
    return nil, err
  }
  client := &http.Client{
    Timeout: 30 * time.Second,
    CheckRedirect: func(next *http.Request, previous []*http.Request) error {
      if len(previous) >= 3 || next.URL.Path != u.Path {
        return errors.New("invalid FCS key redirect")
      }
      _, err := keyURL(next.URL.String())
      return err
    },
  }
  request, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
  if err != nil {
    return nil, err
  }
  response, err := client.Do(request)
  if err != nil {
    return nil, err
  }
  defer response.Body.Close()
  if response.StatusCode != http.StatusOK {
    return nil, fmt.Errorf("Apple FCS key service returned HTTP %d", response.StatusCode)
  }
  data, err := io.ReadAll(io.LimitReader(response.Body, 16385))
  if err != nil || len(data) > 16384 {
    return nil, errors.New("invalid Apple FCS response size")
  }
  return data, nil
}

func unwrap(md map[string][]byte, publicReleaseKey []byte) ([]byte, error) {
  block, rest := pem.Decode(publicReleaseKey)
  if block == nil || block.Type != "PRIVATE KEY" || len(bytes.TrimSpace(rest)) != 0 {
    return nil, errors.New("invalid FCS release key")
  }
  parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
  if err != nil {
    return nil, err
  }
  ecKey, ok := parsed.(*ecdsa.PrivateKey)
  if !ok {
    return nil, errors.New("FCS key must be P-256")
  }
  ecdhKey, err := ecKey.ECDH()
  if err != nil || ecdhKey.Curve() != ecdh.P256() {
    return nil, errors.New("FCS key must be P-256")
  }
  privateKey, err := hpke.NewDHKEMPrivateKey(ecdhKey)
  if err != nil {
    return nil, err
  }
  var response struct {
    EncRequest string `json:"enc-request"`
    WrappedKey string `json:"wrapped-key"`
  }
  if err := json.Unmarshal(md["com.apple.wkms.fcs-response"], &response); err != nil {
    return nil, err
  }
  enc, err := base64.StdEncoding.Strict().DecodeString(response.EncRequest)
  if err != nil || len(enc) != 65 {
    return nil, errors.New("invalid FCS encapsulation")
  }
  wrapped, err := base64.StdEncoding.Strict().DecodeString(response.WrappedKey)
  if err != nil || len(wrapped) != 48 {
    return nil, errors.New("invalid FCS wrapped key")
  }
  recipient, err := hpke.NewRecipient(enc, privateKey, hpke.HKDFSHA256(), hpke.AES256GCM(), nil)
  if err != nil {
    return nil, err
  }
  key, err := recipient.Open(nil, wrapped)
  if err != nil || len(key) != 32 {
    return nil, errors.New("FCS key authentication failed")
  }
  return key, nil
}

func decode(input, expectedDigest, output string) error {
  if runtime.GOOS != "darwin" {
    return errors.New("Recovery image decoding requires macOS")
  }
  digest, err := hex.DecodeString(expectedDigest)
  if err != nil || len(digest) != sha256.Size || hex.EncodeToString(digest) != expectedDigest {
    return errors.New("expected input SHA-256 is required")
  }
  info, err := os.Lstat(input)
  if err != nil || !info.Mode().IsRegular() {
    return errors.New("input must be a regular file")
  }
  if _, err := os.Lstat(output); !errors.Is(err, os.ErrNotExist) {
    return errors.New("output must not already exist")
  }
  // Stage the exact admitted bytes privately. The native decoder never reopens
  // a user-controlled input path after its digest has been checked.
  work, err := os.MkdirTemp(filepath.Dir(output), ".omarchy-restore-")
  if err != nil {
    return err
  }
  defer os.RemoveAll(work)
  source, err := os.Open(input)
  if err != nil {
    return err
  }
  defer source.Close()
  opened, err := source.Stat()
  if err != nil || !os.SameFile(info, opened) {
    return errors.New("input identity changed")
  }
  staged := filepath.Join(work, "BaseSystem.dmg.aea")
  target, err := os.OpenFile(staged, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
  if err != nil {
    return err
  }
  hash := sha256.New()
  _, copyErr := io.Copy(io.MultiWriter(target, hash), source)
  closeErr := target.Close()
  if copyErr != nil || closeErr != nil || !bytes.Equal(hash.Sum(nil), digest) {
    return errors.New("Recovery input digest mismatch or copy failure")
  }
  stream, err := os.Open(staged)
  if err != nil {
    return err
  }
  md, parseErr := metadata(stream)
  stream.Close()
  if parseErr != nil {
    return parseErr
  }
  ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
  defer cancel()
  releaseKey, err := fetchKey(ctx, string(md["com.apple.wkms.fcs-key-url"]))
  if err != nil {
    return err
  }
  key, err := unwrap(md, releaseKey)
  if err != nil {
    return err
  }
  keyPath := filepath.Join(work, "archive.key")
  if err := os.WriteFile(keyPath, key, 0600); err != nil {
    return err
  }
  decoded := filepath.Join(work, "BaseSystem.dmg")
  command := exec.CommandContext(ctx, "/usr/bin/aea", "decrypt", "-i", staged,
    "-o", decoded, "-key", keyPath)
  command.Stderr = os.Stderr
  if err := command.Run(); err != nil {
    return fmt.Errorf("native AEA authentication/decryption failed: %w", err)
  }
  command = exec.CommandContext(ctx, "/usr/bin/hdiutil", "imageinfo", "-plist", decoded)
  command.Stderr = os.Stderr
  if err := command.Run(); err != nil {
    return fmt.Errorf("decoded Recovery is not a recognized disk image: %w", err)
  }
  if err := os.Chmod(decoded, 0600); err != nil {
    return err
  }
  // Atomic no-clobber publication; a concurrent destination is never replaced.
  return os.Link(decoded, output)
}

func verifyStage1(input, expectedDigest, expectedSize string) error {
  size, err := strconv.ParseInt(expectedSize, 10, 64)
  if err != nil || size <= 2048 || size > 16*1024*1024 || size%16384 != 0 {
    return errors.New("invalid raw stage-1 size or alignment")
  }
  digest, err := hex.DecodeString(expectedDigest)
  if err != nil || len(digest) != sha256.Size || hex.EncodeToString(digest) != expectedDigest {
    return errors.New("invalid raw stage-1 SHA-256")
  }
  info, err := os.Lstat(input)
  if err != nil || !info.Mode().IsRegular() || info.Size() != size {
    return errors.New("raw stage 1 is missing or changed")
  }
  file, err := os.Open(input)
  if err != nil {
    return err
  }
  defer file.Close()
  opened, err := file.Stat()
  if err != nil || !os.SameFile(info, opened) {
    return errors.New("raw stage-1 identity changed")
  }
  hash := sha256.New()
  copied, err := io.Copy(hash, io.LimitReader(file, size+1))
  if err != nil || copied != size || !bytes.Equal(hash.Sum(nil), digest) {
    return errors.New("raw stage-1 digest mismatch")
  }
  return nil
}

func main() {
  if len(os.Args) == 5 && os.Args[1] == "verify-stage1" {
    if err := verifyStage1(os.Args[2], os.Args[3], os.Args[4]); err != nil {
      fmt.Fprintln(os.Stderr, "omarchy-restore-image:", err)
      os.Exit(1)
    }
    return
  }
  if len(os.Args) != 4 {
    fmt.Fprintln(os.Stderr, "usage: omarchy-restore-image INPUT_AEA INPUT_SHA256 OUTPUT_DMG")
    os.Exit(64)
  }
  if err := decode(os.Args[1], os.Args[2], os.Args[3]); err != nil {
    fmt.Fprintln(os.Stderr, "omarchy-restore-image:", err)
    os.Exit(1)
  }
}
