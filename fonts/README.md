# Fonts

BDF bitmap fonts for the LED matrix. `graphics.Font().LoadFont()` from
`rpi-rgb-led-matrix` only reads BDF — TTF/OTF will not work.

## Bundled fonts

| Font | Cell size | Use |
|------|-----------|-----|
| `4x6.bdf` | 4x6 px | longest lines, ~16 chars visible on a 64px panel |
| `6x10.bdf` | 6x10 px | default, good balance of size and readability |
| `7x13.bdf` | 7x13 px | headlines, short status words |

These come from [hzeller/rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix/tree/master/fonts)
and are the X11 `misc-fixed` fonts, which are **public domain**
(see the upstream `fonts/README`).

## Adding more fonts

Drop any `.bdf` file in here and reference it by filename in the profile:

```yaml
displayRules:
  my-rule:
    font: 7x13.bdf
```

Fetch the full upstream set with `task fetch-fonts`.

Generate your own from a TTF/OTF with [`otf2bdf`](https://github.com/jirutka/otf2bdf):

```bash
sudo apt-get install otf2bdf
otf2bdf -v -o myfont.bdf -r 72 -p 12 /path/to/font.ttf
```

## Lookup order

`led_catcher.display.modes` resolves a font name against, in order:

1. `$FONTS_DIR` (if set)
2. this directory, relative to the repo/install root
3. `/app/fonts` (container layout)
4. the name as an absolute path

If the named font is not found, it falls back to `$LED_DEFAULT_FONT`
(default `6x10.bdf`).
