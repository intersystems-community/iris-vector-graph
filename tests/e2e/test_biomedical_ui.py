#!/usr/bin/env python3
"""E2E UX tests for Biomedical demo application using agent-browser"""
import os
import shutil
import subprocess
import time

import pytest

# Check for required tools and environment
has_agent_browser = shutil.which("agent-browser") is not None
no_display = os.environ.get('DISPLAY') is None and os.environ.get('WAYLAND_DISPLAY') is None
is_headless = os.environ.get('HEADLESS', 'true').lower() == 'true'

# Skip logic for environments without browser capability
skip_reason = ""
if not has_agent_browser:
    skip_reason = "agent-browser CLI not found"
elif no_display and not is_headless:
    skip_reason = "No display available and HEADLESS is not set to true"

def run_agent_browser(commands: str):
    """Run agent-browser commands in a single session and return output"""
    # Use a unique session name for each call to ensure isolation.
    session_name = f"e2e_bio_{time.time_ns()}"
    headless_flag = "" if is_headless else " --headed"
    
    last_stdout = ""
    for line in commands.strip().split("\n"):
        command = line.strip()
        if not command:
            continue
            
        full_command = f"agent-browser --session {session_name}{headless_flag} {command}"
        result = subprocess.run(full_command, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"agent-browser failed on '{command}': {result.stderr or result.stdout}")
        last_stdout = result.stdout
        
    return last_stdout


@pytest.mark.skipif(skip_reason != "", reason=skip_reason)
@pytest.mark.e2e
def test_biomedical_ui_navigation():
    """Test that the biomedical demo page loads and interactive elements exist"""
    # Open the home page and navigate to bio in a single block
    snapshot = run_agent_browser("""
        open http://127.0.0.1:8200/
        wait 2000
        find role link click --name 'View biomedical demo'
        wait 2000
        snapshot
    """)
    assert "IRIS Biomedical Research" in snapshot
    assert "View Architecture" in snapshot


@pytest.mark.e2e
def test_biomedical_architecture_popup():
    """Test that the architecture diagram popup opens correctly"""
    snapshot = run_agent_browser("""
        open http://127.0.0.1:8200/bio
        wait 2000
        find role button click --name 'View Architecture'
        wait 1000
        snapshot
    """)
    assert "Biomedical Graph Architecture" in snapshot
    assert "Research UI (FastHTML)" in snapshot


@pytest.mark.e2e
def test_biomedical_scenario_selection():
    """Test that selecting a scenario populates the search form"""
    snapshot = run_agent_browser("""
        open http://127.0.0.1:8200/bio
        wait 2000
        find role button click --name 'Cancer Protein Research'
        wait 1000
        snapshot
    """)
    assert "TP53" in snapshot or "ENSP" in snapshot or "kinase" in snapshot

