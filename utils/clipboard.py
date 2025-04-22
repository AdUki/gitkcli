"""
Clipboard utilities
"""
import sys
import subprocess

def copy_to_clipboard(text):
    """
    Copy text to system clipboard
    
    Args:
        text (str): Text to copy
        
    Returns:
        bool: True if successful, False otherwise
    """
    # Try platform-specific clipboard commands
    if sys.platform == 'win32':
        return _copy_windows(text)
    elif sys.platform == 'darwin':  # macOS
        return _copy_macos(text)
    else:  # Try Linux/Unix
        return _copy_linux(text)

def _copy_windows(text):
    """Copy text to clipboard on Windows"""
    try:
        subprocess.run(['clip'], input=text, text=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False

def _copy_macos(text):
    """Copy text to clipboard on macOS"""
    try:
        subprocess.run(['pbcopy'], input=text, text=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False

def _copy_linux(text):
    """Copy text to clipboard on Linux/Unix"""
    # Try xclip
    try:
        subprocess.run(['xclip', '-selection', 'clipboard'], input=text, text=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        # Try xsel as an alternative
        try:
            subprocess.run(['xsel', '--clipboard', '--input'], input=text, text=True, check=True)
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
