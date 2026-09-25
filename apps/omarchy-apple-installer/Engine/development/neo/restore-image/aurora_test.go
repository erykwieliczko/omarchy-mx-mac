// SPDX-License-Identifier: MIT
package main

import (
	"bytes"
	"testing"

	"github.com/klauspost/compress/zstd"
)

func TestAuroraDecoder(t *testing.T) {
	encoder, err := zstd.NewWriter(nil)
	if err != nil {
		t.Fatal(err)
	}
	defer encoder.Close()
	data := bytes.Repeat([]byte("verified archive"), 100)
	compressed := encoder.EncodeAll(data, nil)
	decoded, err := decodeAurora(bytes.NewReader(compressed))
	if err != nil || !bytes.Equal(decoded, data) {
		t.Fatalf("round trip: %v", err)
	}
	for _, invalid := range [][]byte{
		[]byte("not zstd"), compressed[:len(compressed)-1],
		encoder.EncodeAll(make([]byte, (2<<20)+1), nil),
		make([]byte, (1<<20)+1),
	} {
		if _, err := decodeAuroraBounded(bytes.NewReader(invalid), 1<<20, 2<<20); err == nil {
			t.Fatal("accepted invalid or oversized archive")
		}
	}
}
