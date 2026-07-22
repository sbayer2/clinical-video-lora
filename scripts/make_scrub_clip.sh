#!/usr/bin/env bash
# Generate a synthetic two-angle side-by-side all-intra proxy for the scrub
# prototype (ADC-005 hedge b). testsrc burns in a running clock, so sync and
# frame accuracy are verifiable by eye: both halves must always show the
# same time. Angle B is hue-shifted so the halves are distinguishable.
# (drawtext frame counters would be nicer, but Homebrew ffmpeg builds
# without freetype lack the filter.)
#
# Usage: make_scrub_clip.sh [out.mp4] [duration_secs]
set -euo pipefail

out="${1:-scrub_test_clip.mp4}"
dur="${2:-120}"
rate=30

angle() { # extra filter chain after testsrc
  echo "testsrc=size=1920x1080:rate=${rate}${1}"
}

encode() { # codec args...
  ffmpeg -y -hide_banner -loglevel error \
    -f lavfi -i "$(angle '')" \
    -f lavfi -i "$(angle ',hue=h=140')" \
    -filter_complex hstack -t "$dur" -r "$rate" \
    "$@" -pix_fmt yuv420p -movflags +faststart "$out"
}

# All-intra (-g 1) is the load-bearing encode choice: every frame is a
# keyframe, so a currentTime seek is a one-frame decode. Prefer the
# hardware encoder; fall back to libx264.
if encode -c:v h264_videotoolbox -g 1 -b:v 20M 2>/dev/null; then
  enc="h264_videotoolbox"
else
  encode -c:v libx264 -g 1 -crf 18 -preset fast
  enc="libx264"
fi

echo "wrote $out (${dur}s, 3840x1080 @ ${rate}fps, all-intra H.264 via ${enc})"
echo "open tools/scrub_prototype.html and load it"
