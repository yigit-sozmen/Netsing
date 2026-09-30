# Netsing

Netsing is a Python TUI tool that transforms activities in your network into sounds.

## How does it work?

Firstly, Netsing captures your internet traffic with [Scapy](https://scapy.net/) then uses DSP for audio input and Textual for visual output.

``` Network Traffic -> Scapy -> Textual and DSP -> Visual and Audio Output```

## Requirements 

- **Gentoo**: `sudo emerge --ask media-libs/portaudio`
-  **Arch**: `sudo pacman -S portaudio`
-  **Debian/Ubuntu**: `sudo apt install portaudio19-dev`

## Running Netsing

To basically run Netsing use:

```sudo python main.py```

## AI Usage and License

Netsing's user interface and sound generation coded using AI because I lack of skill of developing an interface.

Netsing is licensed with MIT License.

## Contributing

Contributions and suggestions are always welcome !

**Need contributions mostly for interface.**
