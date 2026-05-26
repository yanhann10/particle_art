#!/bin/bash
#
# Setup script for hyperparameter sweep infrastructure.
# Initializes manifest and makes all scripts executable.

set -euo pipefail

REPO_DIR="${HOME}/git_repo/particle_art"
SCRIPTS_DIR="${REPO_DIR}/scripts"
SKILL_DIR="${HOME}/.claude/skills/hyperparam-sweep"

echo "Setting up hyperparameter sweep..."

# Make scripts executable
chmod +x "$SKILL_DIR/sweep.sh"
chmod +x "$SKILL_DIR/sweep.py"
chmod +x "$SCRIPTS_DIR/sweep_coordinator.py"
chmod +x "$SCRIPTS_DIR/parallel_sweep_launcher.sh"

cd "$REPO_DIR"

# Initialize manifest
echo "Initializing sweep manifest..."
python3 "$SCRIPTS_DIR/sweep_coordinator.py" init

echo ""
echo "Setup complete! Manifest initialized with $(jq '.metadata.total_pieces' .sweep_manifest.json) pieces."
echo ""
echo "To start parallel sweeps:"
echo "  1. Open 10 terminal windows"
echo "  2. In each, run: bash ~/git_repo/particle_art/scripts/parallel_sweep_launcher.sh session_N"
echo ""
echo "To monitor progress:"
echo "  python3 ~/git_repo/particle_art/scripts/sweep_coordinator.py status"
