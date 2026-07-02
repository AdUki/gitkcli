#!/usr/bin/env python3
"""
Setup script for GitkCLI
"""

from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="gitkcli",
    version="0.1.0",
    author="Simon Mikuda",
    author_email="simon.mikuda@gmail.com",
    description="A terminal-based Git repository viewer",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/AdUki/gitkcli",
    py_modules=["gitkcli"],  # the entry-point shim
    packages=find_packages(include=["gitk", "gitk.*"]),  # the application package
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Operating System :: OS Independent",
        "Environment :: Console :: Curses",
        "Topic :: Software Development :: Version Control :: Git",
    ],
    # 3.7+: uses `from __future__ import annotations` and datetime.fromisoformat,
    # both introduced in 3.7.
    python_requires=">=3.7",
    install_requires=[
        'windows-curses; sys_platform == "win32"',
    ],
    # pyperclip is optional: the clipboard helper imports it lazily and degrades
    # to a warning when absent. Offer it as an extra: pip install gitkcli[clipboard]
    extras_require={
        "clipboard": ["pyperclip"],
    },
    entry_points={
        "console_scripts": [
            "gitkcli=gitk.main:main",
        ],
    },
)
