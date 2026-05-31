# DataMosher

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org)
[![License: GPL v3](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Build Status](https://img.shields.io/badge/build-passing-brightgreen.svg)](https://github.com)

A powerful and flexible Python tool designed for glitch art, video compression manipulation, and creative data-moshing effects. This utility breaks down media files and intentionally corrupts their internal stream data (such as keyframes or macroblocks) to generate unique, abstract visual aesthetics.

## Features

- **Keyframe Elimination:** Automatically strip I-frames from video files to force continuous pixel interpolation (P-frame blending).
- **Delta-Block Duplication:** Repeat specific P-frames or motion vectors to elongate glitch trails.
- **Header Protection:** Intelligent binary analysis to prevent corruption of crucial container headers, ensuring the glitched media remains playable.
- **Customizable Intensity:** Fine-grained controls over corruption frequency, interval spacing, and localized data bending.

## Prerequisites

Before running the application, make sure you have Python installed along with the required dependencies:

- Python 3.8 or higher
- FFmpeg (required for video decoding/encoding streams)
