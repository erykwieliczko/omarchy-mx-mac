// SPDX-License-Identifier: MIT
package main

import (
  "bytes"
  "crypto/ecdh"
  "crypto/ecdsa"
  "crypto/elliptic"
  "crypto/hpke"
  "crypto/rand"
  "crypto/sha256"
  "crypto/x509"
  "encoding/base64"
  "encoding/binary"
  "encoding/json"
  "encoding/pem"
  "fmt"
  "os"
  "path/filepath"
  "testing"
)

func TestStage1VerifierRejectsChangedBytesAndSymlink(t *testing.T) {
  directory := t.TempDir()
  path := filepath.Join(directory, "boot.bin")
  data := bytes.Repeat([]byte{42}, 16384)
  if err := os.WriteFile(path, data, 0600); err != nil {
    t.Fatal(err)
  }
  digest := fmt.Sprintf("%x", sha256.Sum256(data))
  if err := verifyStage1(path, digest, "16384"); err != nil {
    t.Fatal(err)
  }
  for _, size := range []string{"16383", "32768", "0", "-1"} {
    if verifyStage1(path, digest, size) == nil {
      t.Fatal("accepted wrong size", size)
    }
  }
  if verifyStage1(path, fmt.Sprintf("%064d", 0), "16384") == nil {
    t.Fatal("accepted wrong digest")
  }
  link := filepath.Join(directory, "link")
  if err := os.Symlink(path, link); err != nil {
    t.Fatal(err)
  }
  if verifyStage1(link, digest, "16384") == nil {
    t.Fatal("accepted symlink")
  }
}

func testArchive(entries ...[]byte) []byte {
  var body bytes.Buffer
  for _, entry := range entries {
    binary.Write(&body, binary.LittleEndian, uint32(len(entry)+4))
    body.Write(entry)
  }
  var out bytes.Buffer
  out.WriteString("AEA1")
  binary.Write(&out, binary.LittleEndian, uint32(1))
  binary.Write(&out, binary.LittleEndian, uint32(body.Len()))
  out.Write(body.Bytes())
  return out.Bytes()
}

func TestMetadataBoundsAndAmbiguity(t *testing.T) {
  valid := testArchive([]byte("key\x00value"))
  parsed, err := metadata(bytes.NewReader(valid))
  if err != nil || string(parsed["key"]) != "value" {
    t.Fatalf("valid metadata failed: %v", err)
  }
  oversized := append([]byte(nil), valid...)
  binary.LittleEndian.PutUint32(oversized[8:12], maxMetadata+1)
  badEntry := append([]byte(nil), valid...)
  binary.LittleEndian.PutUint32(badEntry[12:16], 3)
  for _, data := range [][]byte{
    valid[:5], valid[:len(valid)-1], oversized, badEntry,
    testArchive([]byte("key\x00one"), []byte("key\x00two")),
    testArchive([]byte("missing-terminator")),
  } {
    if _, err := metadata(bytes.NewReader(data)); err == nil {
      t.Fatalf("accepted malformed metadata: %x", data)
    }
  }
}

func TestOnlyApplePublicFCSURL(t *testing.T) {
  valid := "https://wkms-public.apple.com/fcs-keys/" + string(bytes.Repeat([]byte("a"), 43)) + "="
  if _, err := keyURL(valid); err != nil {
    t.Fatal(err)
  }
  cdn := "https://fcs-keys-pub-prod.cdn-apple.com/fcs-keys/" + string(bytes.Repeat([]byte("a"), 43)) + "="
  if _, err := keyURL(cdn); err != nil {
    t.Fatal(err)
  }
  for _, raw := range []string{
    "http" + valid[5:], valid + "?redirect=1", valid + "#fragment",
    "https://wkms-public.apple.com.evil.invalid/fcs-keys/test",
    "https://user@wkms-public.apple.com/fcs-keys/test",
    "https://wkms-public.apple.com:443/fcs-keys/test",
    "https://wkms-public.apple.com/fcs-keys/../secret",
  } {
    if _, err := keyURL(raw); err == nil {
      t.Fatalf("accepted URL %q", raw)
    }
  }
}

func TestWrappedKeyAuthentication(t *testing.T) {
  private, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
  if err != nil {
    t.Fatal(err)
  }
  der, err := x509.MarshalPKCS8PrivateKey(private)
  if err != nil {
    t.Fatal(err)
  }
  releaseKey := pem.EncodeToMemory(&pem.Block{Type:"PRIVATE KEY", Bytes:der})
  ecKey, err := private.ECDH()
  if err != nil {
    t.Fatal(err)
  }
  publicKey, err := hpke.DHKEM(ecdh.P256()).NewPublicKey(ecKey.PublicKey().Bytes())
  if err != nil {
    t.Fatal(err)
  }
  enc, sender, err := hpke.NewSender(publicKey, hpke.HKDFSHA256(), hpke.AES256GCM(), nil)
  if err != nil {
    t.Fatal(err)
  }
  expected := bytes.Repeat([]byte{42}, 32)
  wrapped, err := sender.Seal(nil, expected)
  if err != nil {
    t.Fatal(err)
  }
  makeMetadata := func(value []byte) map[string][]byte {
    response, _ := json.Marshal(map[string]string{
      "enc-request":base64.StdEncoding.EncodeToString(enc),
      "wrapped-key":base64.StdEncoding.EncodeToString(value),
    })
    return map[string][]byte{"com.apple.wkms.fcs-response":response}
  }
  observed, err := unwrap(makeMetadata(wrapped), releaseKey)
  if err != nil || !bytes.Equal(expected, observed) {
    t.Fatalf("key unwrap failed: %v", err)
  }
  wrapped[0] ^= 1
  if _, err := unwrap(makeMetadata(wrapped), releaseKey); err == nil {
    t.Fatal("accepted forged wrapped key")
  }
}
