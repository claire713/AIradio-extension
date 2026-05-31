# AI-Radio

This repository accompanies the thesis. Full details of the project are provided
in the thesis document.

## Added channels

Two channels were added to the existing **ai-radio** system. They are located in
the `channels/` folder:

- `channels/local.py`
- `channels/cloud.py`

The platform's original built-in channels have been removed, so that the
`channels/` folder contains only the channels developed for this work.

## Configuration

The YAML configuration file was updated to add these channels to the existing
system.

## Audio and other assets (not included)

Some of the assets used by the channels, including audio files are not included in this repository due to their size.
The channels expect these files in the `channels/audio/` folder.

## Existing code

Any remaining code in this repository belongs to the pre-existing ai-radio
platform and was not developed as part of this work.

## Results

The `results/` folder contains example logs and testing outputs.
