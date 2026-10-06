#!/bin/zsh
set -euo pipefail

bundle_dir=${0:A:h}
dataset_dir="$bundle_dir/reefplot-248"
openreef_dir=${OPENREEF_DIR:-$HOME/openreef}
results_dir="$bundle_dir/results"
result_path="$results_dir/openreef-benchmark-mac.json"

finish() {
  status=$?
  echo
  if (( status == 0 )); then
    echo "Benchmark complete: $result_path"
  else
    echo "Benchmark stopped with status $status. Review the messages above."
  fi
  if [[ -t 0 ]]; then
    echo "Press any key to close this window."
    read -k 1
  fi
  exit $status
}
trap finish EXIT

echo "OpenReef local compute benchmark"
echo "Dataset: $dataset_dir"
echo "OpenReef: $openreef_dir"
echo

if [[ ! -x "$openreef_dir/scripts/openreef-pipeline.sh" ]]; then
  echo "OpenReef is not installed at $openreef_dir."
  echo "Install the desktop OpenReef checkout there, or run with:"
  echo "  OPENREEF_DIR=/path/to/openreef $0"
  exit 2
fi

echo "Verifying the identical 248-image dataset..."
(cd "$dataset_dir" && shasum -a 256 -c "$bundle_dir/IMAGE_MANIFEST.sha256")
mkdir -p "$results_dir"

python3 "$bundle_dir/benchmark-local.py" \
  "$dataset_dir" \
  --openreef-dir "$openreef_dir" \
  --output "$result_path"
