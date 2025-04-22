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
    author="Your Name",
    author_email="your.email@example.com",
    description="A terminal-based Git repository viewer",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/gitkcli",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Environment :: Console :: Curses",
        "Topic :: Software Development :: Version Control :: Git",
    ],
    python_requires=">=3.6",
    install_requires=[
        "pygit2>=1.5.0",
    ],
    entry_points={
        "console_scripts": [
            "gitkcli=gitkcli.gitkcli:main",
        ],
    },
)
