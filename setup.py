#!/usr/bin/env python3
"""
Setup script for GitkCLI
"""
from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="gitkcli",
    version="0.1.0",
    author="Simon Mikuda",
    author_email="simon.mikuda@example.com",
    description="A terminal-based Git repository viewer",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/AdUki/gitkcli",
    py_modules=["gitkcli"],   # the entry-point shim
    packages=find_packages(include=["gitk", "gitk.*"]),  # the application package
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Environment :: Console :: Curses",
        "Topic :: Software Development :: Version Control :: Git",
    ],
    python_requires=">=3.6",
    install_requires=[
        'windows-curses; sys_platform == "win32"',
    ],
    entry_points={
        "console_scripts": [
            "gitkcli=gitk.main:main",
        ],
    },
)

