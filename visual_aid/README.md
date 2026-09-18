# Visual Aid

Images and GIFs shown by the `image` and `gif` display modes. Reference them
by filename in the profile:

```yaml
displayRules:
  github-error:
    kind: gif
    image: sunset.gif
```

Anything Pillow can open works. Frames are scaled to the panel at display time,
keeping their aspect ratio — a non-square source is fitted and centred, not
stretched — so sources do not need to be pre-sized. Small files still keep GIF
playback smooth on a Pi.

## What ships here

| File | Used by | What it is |
|------|---------|------------|
| `sunset.gif` | `github-error` in `tests/profile.yaml`, `led-catcher-publish --demo` | 64x64, 24 frames. A sun setting into a sea and rising again — a seamless loop over a full-height gradient, which is the case a panel renders worst |
| `test-pattern.png` | `demo-pattern` in `tests/profile.yaml`, `led-catcher-publish --demo` | 64x64 alignment and colour pattern for bringing a panel up: a 1px border, per-channel corner marks, RGB+white ramps, a 1px checkerboard and a centre cross |

Both are drawn by `hack/generate_demo_assets.py` and ship under this
repository's licence. Redraw them with:

```bash
task demo-assets
```

They are drawn rather than downloaded or carried over from the old
`homerun-matrix-catcher` Pi on purpose: the legacy GIFs (nyan, explosion, …)
are of unclear provenance, and this way `kind: image` and `kind: gif` work on a
fresh checkout with no network and no licence to establish.

## Lookup order

`led_catcher.display.modes` resolves an image name against, in order:

1. `$VISUAL_AID_DIR` (if set)
2. this directory, relative to the repo/install root
3. `/app/visual_aid` (container layout)
4. the name as an absolute path

A deployment that wants its own images sets `$VISUAL_AID_DIR` (a
ConfigMap/hostPath in Kubernetes, a plain directory on the Pi); nothing here has
to be removed for that.
