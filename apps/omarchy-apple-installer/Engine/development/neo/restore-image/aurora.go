// SPDX-License-Identifier: MIT
package main

import (
	"errors"
	"io"
	"os"

	"github.com/klauspost/compress/zstd"
)

const maxAuroraArchive = 384 << 20
const maxAuroraTar = 456 << 20

func decodeAurora(input io.Reader) ([]byte, error) {
	return decodeAuroraBounded(input, maxAuroraArchive, maxAuroraTar)
}

func decodeAuroraBounded(input io.Reader, compressedLimit, tarLimit int) ([]byte, error) {
	compressed, err := io.ReadAll(io.LimitReader(input, int64(compressedLimit)+1))
	if err != nil || len(compressed) > compressedLimit {
		return nil, errors.New("Aurora compressed archive exceeds limit")
	}
	decoder, err := zstd.NewReader(nil, zstd.WithDecoderConcurrency(1),
		zstd.WithDecoderMaxMemory(uint64(tarLimit)), zstd.WithDecoderMaxWindow(uint64(tarLimit)))
	if err != nil {
		return nil, err
	}
	defer decoder.Close()
	return decoder.DecodeAll(compressed, nil)
}

func decodeAuroraFile(path string) error {
	input, err := os.Open(path)
	if err != nil {
		return err
	}
	defer input.Close()
	decoded, err := decodeAurora(input)
	if err != nil {
		return err
	}
	_, err = os.Stdout.Write(decoded)
	return err
}
