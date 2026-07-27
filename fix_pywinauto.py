#!/usr/bin/env python3
"""
Fix pywinauto compatibility issues.

The error "find_elements() got an unexpected keyword argument 'timeout'" 
occurs because the installed version of pywinauto has a version conflict.

This script installs a known-compatible version.
"""

import subprocess
import sys

def install_compatible_pywinauto():
    """Install a compatible version of pywinauto that works with the code."""
    print("=" * 70)
    print("FIXING PYWINAUTO COMPATIBILITY ISSUE")
    print("=" * 70)
    print()
    print("The installed pywinauto version is incompatible with the code.")
    print("Installing compatible version...")
    print()
    
    # Remove current version
    print("[1/3] Removing current pywinauto version...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "pywinauto", "-y"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print("      ✓ Removed")
    else:
        print("      ! Could not uninstall (may not be installed)")
    
    # Install compatible version
    print("[2/3] Installing compatible pywinauto version 0.6.11...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "pywinauto==0.6.11", "--upgrade"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print("      ✓ Installed")
    else:
        print("      ✗ Installation failed!")
        print(result.stderr)
        return False
    
    # Verify installation
    print("[3/3] Verifying installation...")
    try:
        import pywinauto
        print(f"      ✓ pywinauto {pywinauto.__version__} ready")
    except ImportError:
        print("      ✗ Verification failed")
        return False
    
    print()
    print("=" * 70)
    print("✓ FIXED! Restart your application and try again.")
    print("=" * 70)
    return True

if __name__ == "__main__":
    success = install_compatible_pywinauto()
    sys.exit(0 if success else 1)
