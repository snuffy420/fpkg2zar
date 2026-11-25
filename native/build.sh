#!/bin/sh
# Builds the Kraken decoder for macOS (fpkg2zar/libkraken.dylib) or Linux (fpkg2zar/libkraken.so).
set -e
cd "$(dirname "$0")"
CC="${CC:-cc}"
case "$(uname -s)" in
  Darwin)
    # Universal binary: runs on Apple Silicon and Intel Macs.
    $CC -O2 -Wall -dynamiclib -arch arm64 -arch x86_64 kraken.c -o ../fpkg2zar/libkraken.dylib
    echo "Built fpkg2zar/libkraken.dylib"
    ;;
  *)
    $CC -O2 -Wall -shared -fPIC kraken.c -o ../fpkg2zar/libkraken.so
    echo "Built fpkg2zar/libkraken.so"
    ;;
esac
