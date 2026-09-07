// Read-only, unprivileged file extraction and physical-read tracing.
package main

import (
  "crypto/sha256"
  "encoding/hex"
  "encoding/json"
  "fmt"
  "io"
  "io/fs"
  "os"
  "path"
  "path/filepath"
  "strings"

  "github.com/deploymenttheory/go-apfs-v2/pkg/apfs"
)

type span struct {
  Offset int64 `json:"offset"`
  Size int `json:"size"`
}

type traced struct {
  file *os.File
  reads []span
}

func (t *traced) ReadAt(p []byte, off int64) (int, error) {
  n, err := t.file.ReadAt(p, off)
  if n > 0 { t.reads = append(t.reads, span{off, n}) }
  return n, err
}

type selection struct {
  Source string `json:"source"`
  Name string `json:"name"`
  Transform string `json:"transform"`
  Expected string `json:"expected_sha256,omitempty"`
}

type result struct {
  Selection selection `json:"selection"`
  Size int `json:"size"`
  SHA256 string `json:"sha256"`
  Reads []span `json:"reads"`
  Compressed bool `json:"compressed"`
  Storage []string `json:"storage"`
}

func run() error {
  if len(os.Args) != 4 { return fmt.Errorf("usage: apfs-reader IMAGE SELECTION_JSON NEW_OUTPUT_DIR") }
  selectionBytes, err := os.ReadFile(os.Args[2]); if err != nil { return err }
  var selections []selection
  if err := json.Unmarshal(selectionBytes, &selections); err != nil { return err }
  if len(selections) == 0 || len(selections) > 128 { return fmt.Errorf("invalid file count") }
  if err := os.Mkdir(os.Args[3], 0700); err != nil { return err }
  file, err := os.Open(os.Args[1]); if err != nil { return err }; defer file.Close()
  var results []result
  for _, selected := range selections {
    if !fs.ValidPath(selected.Source) || !fs.ValidPath(selected.Name) || selected.Name == "." {
      return fmt.Errorf("invalid source/output path")
    }
    reader := &traced{file: file}
    container, err := apfs.Open(reader, nil); if err != nil { return err }
    volume, err := container.VolumeBySelector("0"); if err != nil { return err }
    source := selected.Source
    var info fs.FileInfo
    for links := 0; links < 16; links++ {
      info, err = volume.Stat(source); if err != nil { return err }
      if info.Mode() & fs.ModeSymlink == 0 { break }
      target, err := volume.Readlink(source); if err != nil { return err }
      if strings.HasPrefix(target, "/") { source = strings.TrimPrefix(path.Clean(target), "/")
      } else { source = path.Join(path.Dir(source), target) }
      if !fs.ValidPath(source) { return fmt.Errorf("symlink escapes image") }
    }
    if info == nil || !info.Mode().IsRegular() || info.Size() > 64*1024*1024 {
      return fmt.Errorf("requires regular file up to 64 MiB: %s", source)
    }
    entry, err := volume.FileEntryByPath("/"+source); if err != nil { return err }
    size, err := entry.Size(); if err != nil { return err }
    data := make([]byte, size)
    n, err := entry.ReadAt(data, 0)
    if err != nil && err != io.EOF { return err }
    if uint64(n) != size { return fmt.Errorf("short file read") }
    sum := sha256.Sum256(data)
    output := filepath.Join(os.Args[3], selected.Name)
    if err := os.MkdirAll(filepath.Dir(output), 0700); err != nil { return err }
    if err := os.WriteFile(output, data, 0600); err != nil { return err }
    storage := []string{selected.Name}
    if entry.CompressedDataHeader != nil {
      attrs, err := volume.Xattrs(source); if err != nil { return err }
      storage = []string{selected.Name+".decmpfs"}
      if err := os.WriteFile(output+".decmpfs", attrs["com.apple.decmpfs"], 0600); err != nil { return err }
      if entry.CompressedDataHeader.CompressionMethod % 2 == 0 {
        storage = append(storage, selected.Name+".resource")
        if err := os.WriteFile(output+".resource", attrs["com.apple.ResourceFork"], 0600); err != nil { return err }
      }
    }
    results = append(results, result{selected, n, hex.EncodeToString(sum[:]), reader.reads, entry.CompressedDataHeader != nil, storage})
    container.Close()
  }
  return json.NewEncoder(os.Stdout).Encode(results)
}

func main() {
  if len(os.Args) == 5 && os.Args[1] == "decode" {
    if err := decodeStorage(os.Args[2], os.Args[3], os.Args[4]); err != nil {
      fmt.Fprintln(os.Stderr, err); os.Exit(1)
    }
    return
  }
  if err := run(); err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(1) }
}

func decodeStorage(attrPath, resourcePath, output string) error {
  attr, err := os.ReadFile(attrPath); if err != nil { return err }
  header, err := apfs.ParseCompressedDataHeader(attr)
  if err != nil { return err }
  if header == nil || header.UncompressedDataSize > 64*1024*1024 { return fmt.Errorf("invalid decmpfs header") }
  methods := map[uint32]int{3:1,4:1,7:3,8:3,11:2,12:2}
  method, ok := methods[header.CompressionMethod]
  if !ok { return fmt.Errorf("unsupported decmpfs type %d", header.CompressionMethod) }
  data := attr
  if header.CompressionMethod % 2 == 0 {
    data, err = os.ReadFile(resourcePath); if err != nil { return err }
  }
  source, err := apfs.NewDataStreamFromData(data); if err != nil { return err }
  stream, err := apfs.NewCompressedDataHandle(source, header.UncompressedDataSize, method)
  if err != nil { return err }
  defer stream.Close()
  decoded := make([]byte, header.UncompressedDataSize)
  n, err := stream.ReadSegmentData(0, decoded)
  if err != nil && err != io.EOF { return err }
  if uint64(n) != header.UncompressedDataSize { return fmt.Errorf("decoded file length mismatch") }
  f, err := os.OpenFile(output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600); if err != nil { return err }
  defer f.Close()
  _, err = f.Write(decoded)
  return err
}
