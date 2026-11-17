# Tamis

Tamis is a PyQt6 desktop application for rapidly reviewing and triaging large photo shoots. It focuses on fast image loading (embedded RAW previews, background decoding and prefetching), keyboard-driven marking, and one-click sorting of files into keep/reject folders.

## Features
- Supports JPEG, PNG, WebP, TIFF, HEIC (when Qt has a plugin for it) and common RAW formats (ARW, CR2, CR3, NEF, DNG, ORF, RAF, RW2) via `rawpy`.
- **Cull** view: the current photo fitted to the window with a filmstrip; next/previous photos are prefetched so stepping through is instant. Double-click or `Z` zooms to 1:1 (the photo is loaded at full resolution for focus checking), drag to pan. The zoom and its position stay as you move to the next photo, so a burst can be checked at the same spot. An edited photo shows its edit at 1:1 too, rendered at full resolution in the background (the original shows in its frame until then), so grain or a film simulation can be checked up close.
- **Zoom on faces** (`⇧Z`, in the Cull view and Compare): zooms each photo to 1:1 on its own main face, at the same scale, since the subject moves a little between the frames of a burst; panning then moves them all by as much. A photo without a face keeps the shared zoom. Needs face detection (macOS, below).
- **Keeps only**: `K` (or the chip in the Cull tab's corner) limits the filmstrip and all navigation to the photos marked keep, for a second pass. Rejecting or unmarking a photo moves on to the next keep; picking another photo in the Gallery, or `N`, ends the filter.
- **Compare**: `C` shows photos side by side (the current and next one, or the 2–4 selected in the Gallery) with shared zoom and pan. `←`/`→` pick the photo that marks apply to.
- **Gallery** view: thumbnail grid with multi-select marking and adjustable thumbnail size. Filters combine: status (All / Unmarked / Keeps / Rejects), minimum stars, colour labels (several can be on) and focus, and follow changes live.
- **Sorting**: by name, with numbers compared as numbers (`IMG_9` before `IMG_10`), by capture date (with sub-seconds; a file without a date uses its modification time) or by sharpness. The choice is remembered, and dates are read in the background, so the order changes once they are all known.
- **Bursts** (`B`, or the chip in the Gallery): shots less than 2 seconds apart are grouped in the filmstrip and Gallery with a coloured bar and their place in the burst (`2/5`), unless the picture changes completely. `⇧P` keeps the current photo and rejects the rest of its burst, as one undo step.
- **Sharpness**, computed in the background when something needs it (the sort, the focus filter, bursts): the variance of the Laplacian in the sharpest regions of a photo. It is only a suggestion: `◎` marks the sharpest frame of a burst and `soft` a photo below a fifth of the folder's median, and marks stay yours. Scores are compared within a folder. On seven culled shoots, `soft` flagged 3% of the photos kept, and 84% of what it flagged had been rejected; in bursts, the frame kept was usually the sharpest or nearly. Sharp photos with little fine detail (shallow depth of field, smooth faces, plain or dark backgrounds) score low, so a `soft` photo is worth a look before rejecting it.
- **Closed eyes and soft faces** (macOS, off by default: **Options ▾ > Look for Closed Eyes and Soft Faces**): Apple's Vision framework finds faces in the background (on the Neural Engine, no model shipped), measures how open the eyes are and scores the sharpness of the faces alone, since a sharp background can hide a soft face. Thumbnails say `eyes closed` or `face soft` (a face below 30% of the folder's median face sharpness; a group is as sharp as its softest face, and faces far smaller than the largest are ignored), the info overlay too, and the Gallery's focus filter gets **Eyes closed or face soft**. In a burst, `◎` then goes to the frame with the sharpest faces where nobody blinks. Only suggestions, remembered per file like the other scores; the threshold is a first guess, not tuned on real shoots yet. Elsewhere, or without `pyobjc-framework-Vision` (installed with Tamis on macOS), the option isn't shown.
- **Focus peaking** (`F` in the Cull view): highlights the sharp edges of what is shown, edits, crop and 1:1 zoom included.
- **Import** (`⌘I`): finds the photos in the sub-folders of a memory card, copies them with their sidecars to a folder and optionally to a backup, reads every copy back to compare it with the original, then opens the folder. Nothing on the card changes, files never overwrite others (`IMG_1 (1).CR2`), and those already imported are skipped, even if sorted into `_KEEPS` or `_REJECTS` since.
- **Trash Rejects**: moves the photos in `_REJECTS` that are still marked reject to the system Trash, with their pairs and sidecars, after a confirmation with their count and size. Nothing is ever deleted: a photo that can't go to the Trash stays where it is, whole.
- Thumbnails are kept on disk (up to 512 MB, in the app's cache folder), so reopening a folder is fast. They are found by a file's path, size and modification time, so a changed or moved file just gets a new one.
- **Edited thumbnails**: the filmstrip and the Gallery show photos with their edit and frame, rendered in the background at low priority and kept on disk per edit; until then they show the original.
- **Star ratings and colour labels**, written to XMP sidecars (`xmp:Rating`, `xmp:Label`) so Lightroom, Bridge, Capture One and darktable see them. Existing sidecars are edited in place, touching only those two properties; new ones are only created for RAWs (`IMG.xmp`).
- **Title, caption and keywords** (`⌘K`, on the current or selected photos): written to the XMP sidecar (`dc:title`, `dc:description`, `dc:subject`, which Lightroom shows as Title, Caption and Keywords; only those properties are touched) and the session, and into exports. A sidecar's own values win, as for ratings; with several photos, a field that differs between them is left alone unless you type in it. Undoable.
- **List of keeps for other editors** (**Options ▾ > List of Keeps for Other Editors…**): the names of the keeps, or of the photos the Gallery shows, to copy or save: for Lightroom Classic's Library filter (**Text › Filename › Contains**, names without extension separated by spaces, so a RAW and its JPEG both match), one file name per line (Capture One, Photo Mechanic…) or a CSV with marks, stars and labels.
- **Keep and reject in other editors**, off by default (**Options ▾ > Show Keep and Reject in Other Editors**), as no flag is common to them. *Rejected as rating −1* writes the `xmp:Rating="-1"` that darktable and Bridge read as rejected; the photo keeps its stars in Tamis, but other editors no longer see them. *Keep green, reject red* gives photos those colour labels, for editors that read `xmp:Label` such as Lightroom, whose own flags are not in XMP at all. A label you gave a photo yourself stays, except that unmarking clears a green or red one. As with ratings, new sidecars are only created for RAWs.
- **RAW+JPEG pairs** (same name, same folder) are culled as one photo and always move together.
- **Shooting info** (exposure, aperture, ISO, focal length, camera, lens, date) read from EXIF; `I` shows or hides it.
- **Quick edits** (`E`): exposure, contrast, highlights, shadows, white balance (temperature / tint), saturation, film simulation LUTs with strength, grain and vignette. Edits are non-destructive (stored in the session, originals untouched), previewed live, can be copied and pasted across photos, and exported as JPEGs (see **Export settings**). RAWs are edited from LibRaw's 16-bit linear development, not from their embedded JPEG: with the edit panel open, a RAW shows that development even before it is edited, so the first slider moved doesn't change the whole picture, and the panel says so. Highlights are kept rather than clipped: the development uses one fixed brightness for every photo (so an edit pasted over a burst looks even) and rolls the brightest tones off, so **Exposure** and **Highlights** can bring them back. RAW edits made before this keep their earlier development, which clipped the brightest 1%, until **Use the New Development** in the panel switches them, with the exposure adjusted so that the photo stays as bright (undoable). Other images are converted to sRGB from their own colour space (Adobe RGB, Display P3…), for culling as for editing, and 16-bit TIFFs and PNGs are edited at 16 bits.
- **Advanced tab** of the edit panel, for finer work; the Basic tab stays as it was for quick edits while culling. The tab's title gets a dot when the photo uses any of it, and so do its sections, so nothing is hidden; Reset, copy and paste and presets cover both tabs. In it:
  - **Auto Tone** sets exposure, contrast, highlights, shadows, whites and blacks from the histogram, **Auto WB** takes away a colour cast, and **Pick WB** makes the point you click neutral grey. Each is an ordinary, undoable change to the sliders, a starting point to adjust.
  - **Whites** and **Blacks**, which move the ends of the tones next to Highlights and Shadows.
  - **Tone curve**: a point curve for RGB and for each channel, drawn over the histogram (click to add a point and drag it, double-click it to remove it), and a parametric curve (highlights, lights, darks, shadows, with movable splits). Curves never overshoot between points.
  - **Colour mixer**: **Vibrance** (saturates muted colours more than vivid ones, and skin tones less), and hue, saturation and luminance for eight colour ranges (red, orange, yellow, green, aqua, blue, purple, magenta), blended smoothly from one range to the next. The target (◎) changes the range under the pointer: drag up or down on the photo.
  - **Colour grading**: a wheel of hue and strength, with a luminance, for the shadows, midtones and highlights and one for the whole picture, with blending and balance.
  - **Black & white**, with how bright each colour range becomes in grey; colour grading then tones it.
  - **Detail**: texture (fine detail), clarity (midtone contrast at a larger radius, through an edge-aware filter that leaves no halos) and dehaze; sharpening (amount, radius, detail, and masking, which keeps it to the edges: hold `⌥` while dragging it to see what is sharpened); luminance and colour noise reduction. RAWs get a little sharpening and colour noise reduction by default, like in other editors. These read a pixel's neighbourhood, with radii in proportion to the picture, so a preview and a full-size export look the same; check them at 1:1, where the edit is rendered at full resolution.
  - **Lens corrections**: distortion, vignetting and lateral chromatic aberration from the lens's profile in the [lensfun](https://lensfun.github.io) database, found from the camera and lens in the EXIF, each with an amount; and the same by hand, with a defringe slider for purple and green fringes. Tamis reads lensfun's XML files directly: it uses the database where lensfun is installed (Homebrew, Linux packages, `~/.local/share/lensfun`) or the folder chosen with **Lens Profiles Folder…** (a copy of lensfun's `data/db`).
  - **Profile** (RAWs): how the camera's colours are rendered. *Standard* is the development Tamis always used, *Neutral* a flatter one, and DCP profiles (the format of Adobe and RawTherapee) are read from the folder chosen with **Folder…** and from Adobe's own folders when they are there, which gives the *Camera Matching* profiles to those who have Adobe's software (Tamis can't ship them). A DCP is applied to the camera's own colours, with its forward matrix, hue and saturation map, look table and tone curve. **Default for Camera** uses the photo's profile for every RAW of that camera that has none of its own.
  - **Masks**: local adjustments with their own exposure, contrast, highlights, shadows, whites, blacks, temperature, tint, saturation, texture, clarity, dehaze and sharpness. A mask is made of linear gradients, radial gradients (drawn on the photo, then moved by their handles), brush strokes (size, feather, flow, and erase with `⌥`), and automatic masks of the **subject** and **people** (Apple's Vision, macOS 14 and later) or the **sky** (found from its colours, everywhere), with edges refined to follow the picture's. Parts are added to, subtracted from or intersected with the selected mask, which can be inverted and shown in red over the photo. Masks are placed on the photo itself, so they stay put through a crop or a turn; automatic ones are worked out once per photo and kept in the app's cache folder.
  - **Spot removal** (`⇧Q`): click a spot or paint over a blemish; a source nearby that looks like its surroundings is found and can be moved, `⌥`-click removes a spot. *Heal* matches the surroundings' brightness and colour, *Clone* copies as is, and **Visualise spots** shows the photo's details in grey, where sensor dust stands out.
  - **History & snapshots**: each change to a photo's edit (slider, preset, paste, mask…) is a step of its history; clicking one goes back to it. **Save Snapshot…** keeps a version of the edit under a name, to come back to it. Both are kept in the session, with the 60 newest steps.
- **Virtual copies** (`⌘'`): a second photo on the same files, with its own marks, stars, label, edit, history and description, to try a black and white version, or several crops for different uses. It follows its photo in the filmstrip and the Gallery (`IMG_1.CR2 · copy 1`) and is saved in the session with it. Process Files moves the files once, with the photo's mark, or to `_KEEPS` when a copy is kept, and exports each copy on its own (`IMG_1-1.jpg`); Trash Rejects leaves files that a copy still uses. `⌘⇧⌫` removes virtual copies (never a photo), asking first when they hold marks or edits.
- **Histogram and clipping**: the edit panel shows the red, green and blue histogram of what is on screen, updated in the background, with the share of the photo clipped at either end. `J` (or **Clipping**) shows them in the Cull view, panel open or not: red where all three channels are at the top (blown out, no detail left), a discreet orange where only one or two are (a vivid colour, flatter but not blown out), and blue where all three are at the bottom. The histogram's figures only count the red and the blue, so a very colourful photo doesn't look overexposed.
- **Before / after**: hold `Y` or `\` (or the **Before** button) to see the photo without its edit, in the same frame; the edit itself doesn't change. It shows the same preview as an unedited photo; with the edit panel open, a RAW shows its own development there, not the camera's embedded JPEG.
- **Rotate and straighten**: `]` / `⇧R` and `[` / `⇧L` turn the current or selected photos by 90°, while culling too; the **Straighten** slider levels a horizon by up to 15°, enlarging the picture so that no empty corner shows. A crop stays on the same part of the picture through a quarter turn. Like a crop, these belong to their photo's framing: they are undoable, exported and cleared by Reset, but neither copied nor kept in a preset, and vignette and grain follow the final frame.
- **Presets**: **Presets > Save Current Look…** keeps a look (without its framing) under a name, and choosing it applies it to the current or selected photos, on this shoot or any other, as one undo step. They are stored in `presets.json` in the app's data folder.
- **Crop** (`R`): drag the frame's corners and edges, or inside it to move it, with a free or fixed shape (original, 1:1, 3:2, 4:5, 16:9…). `Enter`, `R` or a double-click applies it, `Esc` cancels; moving to another photo applies it too. Vignette and grain follow the final frame. A crop is part of the photo's edit (undoable, exported, cleared by Reset), but it stays with its photo: copying and pasting an edit carries the look over and leaves each photo's own crop alone.
- Folders are read in the background, so a slow disk or network share doesn't freeze the window: the folder open until then stays usable, and a count of the photos found shows while it takes a while. New or removed files are picked up automatically while the folder is open (or with **Refresh**, `⌘R`): only the new ones are read, and the rest stays as it is, selection and Compare included. A RAW landing after its JPEG joins it as a pair, with the JPEG's marks.
- Undo for every marking, rating and labelling action.
- Marks are saved automatically in `tamis_session.json` inside each photo folder.
- **Process Files** moves photos into `_KEEPS` / `_REJECTS` (or back to the root when unmarked). XMP sidecars move with their photo, and existing files are never overwritten (`IMG_1 (1).jpg` instead). Unless you untick the export box in its confirmation (ticked by default), it then fills the export folder (`_EXPORTS` unless the export settings say otherwise): edited photos are exported as JPEGs and unedited ones get their JPEG (the photo itself, or the JPEG of a RAW+JPEG pair) copied. The confirmation lets you choose what is exported: *everything except rejects* (the default) or *keeps only*, remembered for next time. Earlier exports of the same photos are replaced, so processing again refreshes them, and exports of photos rejected since are removed, from whichever folder they were written to (Tamis remembers what it exported: other files there, and exports changed since, stay). The confirmation lists the photos without a JPEG, which are not exported (`⌘⇧E` renders them), and those whose film simulation is no longer in the LUT folder, which would be exported without it. A photo whose files can't all be moved stays where it is, with its pair and sidecars, and exports are written to a temporary file first, so a failure never leaves a truncated JPEG behind. Marks made before can still be undone: the photo then moves on the next Process Files.
- **Export settings** (**Options ▾ > Export Settings…**, or the button in the Process Files confirmation): the format (JPEG; 16-bit TIFF, rendered from the 16-bit development before quantisation, for printing or retouching; PNG; HEIF, AVIF and WebP where Qt can write them), the longest edge (full size by default), the quality (92, for JPEG, HEIF, AVIF and WebP), the folder (relative to the shoot folder, such as `_EXPORTS` or `web/large`, or a full path; never the shoot folder itself, `_KEEPS` or `_REJECTS`) and a name pattern made of `{name}` (the original name), `{date}` (capture date, `YYYY-MM-DD`), `{seq}` (`001`, `002`… in the order of one export) and `{folder}` (the shoot folder's name). Save them as named profiles (web, client, print…) to switch between them. Rendered exports are tagged sRGB, with an ICC profile; JPEGs also carry the EXIF of their original (camera, lens, date, exposure, copyright, GPS), with the orientation reset since their pixels are upright. Unedited JPEGs are copied as they are, unless they have to be resized, watermarked or turned into another format. The **Watermark** tab adds a line of text or a PNG logo, in a corner or the centre, sized and placed in percent of the long edge, with an opacity: it is painted after resizing, so it looks the same on any export. A **Hidden mark** (up to 32 bytes of text, such as your name) can be written invisibly into the pixels instead or as well: each 8×8 block carries a bit, changing pixels by less than a level on average, and it survives saving as JPEG down to quality 60, but not resizing, cropping or rotating. **Options ▾ > Read a Hidden Mark…** reads it from a file. The **Credits** tab holds the creator, copyright, contact (e-mail or website) and default keywords, written with each photo's title, caption and keywords into JPEG exports, copied ones included (their pixels untouched), as XMP and as IPTC-IIM for older software; any XMP or IPTC the original had is replaced. Several photos are exported at a time, and a RAW is developed at half its size when that still gives the long edge asked for, which makes small exports much faster.
- Reopens the last folder and photo on launch. Drop a folder onto the window to open it.
- **Options ▾ > About Tamis…** shows the version, and those of Python, Qt and LibRaw, for bug reports, and whether a newer version is out (**Check for Updates Now**).
- **New versions**: at launch, at most once a day, Tamis asks GitHub for its latest release (`api.github.com/repos/Ozraam/Tamis/releases/latest`, in the background, sending nothing but a generic `User-Agent: Tamis/<version>`). If it is newer, a message says so once (click it to open the release page), and About keeps saying it. Pre-releases are ignored, nothing is said when offline, and **Options ▾ > Check for Updates Automatically** turns it off.
- **Options ▾ > Show Log File** opens the log, where warnings, errors (photos that can't be read, failed exports and XMP writes, plugin errors) and the traceback of a crash are written: `~/Library/Logs/Tamis/tamis.log` on macOS, a `logs` folder in the app's data folder elsewhere. Attach it to a bug report.
- **Plugins**: add your own actions and shortcuts with a small Python file (see [Plugins](#plugins)).

## Requirements
- Python 3.10+
- PyQt6 (6.9+), rawpy and numpy, installed from `pyproject.toml`; on macOS also `pyobjc-framework-Vision`, for faces

### Platforms
macOS is the supported platform: it is what Tamis is used and built on, and the only one with a ready-made app (see [Releases](#releases)). CI also runs the tests on Linux and Windows, from source (`python -m tamis`), so that the app keeps working there, but they get less use; reports are welcome. On Windows, a read-only folder can't be simulated in the tests, so falling back to the app's data folder for marks isn't tested there.

## Quick Start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
tamis                  # or: python Tamis.py, python -m tamis
```

## Keyboard Shortcuts
| Action | Keys |
| --- | --- |
| Keep current / selected | `P`, `1`, or `↑` (Cull view) |
| Reject current / selected | `X`, `3`, or `↓` (Cull view) |
| Unmark | `U`, `2`, or `Backspace` |
| Stars 0–5 | `⌘0`–`⌘5` / `Ctrl+0`–`Ctrl+5` |
| Colour label red / yellow / green / blue (again to remove) | `⌘6`–`⌘9` / `Ctrl+6`–`Ctrl+9` |
| Colour label purple (again to remove) | `⌘⇧9` / `Ctrl+Shift+9` |
| Undo | `⌘Z` / `Ctrl+Z` |
| Next / previous photo | `→` `L` `Space` / `←` `H` |
| First / last photo | `Home` / `End` |
| Next unmarked photo | `N` |
| Keeps only in the Cull view, on / off | `K` |
| Zoom 1:1 / back to fit | `Z` or double-click / `Esc` |
| Zoom 1:1 on each photo's main face (macOS) | `⇧Z` / `Shift+Z` |
| Compare (current + next, or the Gallery selection) / leave | `C` / `C` or `Esc` |
| Show / hide shooting info | `I` |
| Edit panel | `E` |
| Crop: open / apply, cancel | `R` or `Enter`, `Esc` |
| Rotate right / left (current or selected) | `]` or `⇧R` / `[` or `⇧L` |
| Show the photo without its edit, while held (Cull view) | `Y` or `\` |
| Clipping warnings, on / off (Cull view) | `J` |
| Title, caption and keywords (current or selected) | `⌘K` / `Ctrl+K` |
| Spot removal, on / off (Cull view) | `⇧Q` / `Shift+Q` |
| Create a virtual copy (current or selected) | `⌘'` / `Ctrl+'` |
| Remove virtual copies (current or selected) | `⌘⇧⌫` / `Ctrl+Shift+Backspace` |
| Copy / paste edit (paste applies to the selection) | `⌘C` / `⌘V` or `⌘⇧C` / `⌘⇧V` (`Ctrl+C` / `V`, `Ctrl+Shift+C` / `V`) |
| Export with the export settings (current or selected photos: edited ones and those without a JPEG are rendered, others copied unless resized, watermarked or in another format) | `⌘⇧E` / `Ctrl+Shift+E` |
| Refresh the folder | `⌘R` / `Ctrl+R`, `F5` |
| Show bursts, on / off | `B` |
| Keep this photo, reject the rest of its burst | `⇧P` / `Shift+P` |
| Focus peaking, on / off (Cull view) | `F` |
| Import from a memory card | `⌘I` / `Ctrl+I` |
| Switch Cull ↔ Gallery | `G` (Enter or double-click in the gallery opens a photo) |
| Open folder | `⌘O` / `Ctrl+O` |
| List every shortcut, the plugins' included | `?` |

These are the defaults. **Customize…** in the `?` overview, or **Options ▾ > Keyboard Shortcuts…**, changes them, with up to three keys per action; a key can only do one thing, so Save waits until no two actions (or an action and a plugin's shortcut) share one, and **Restore Defaults** brings these back. Only the changed ones are saved, in the app's settings. The stats bar's reminder follows the keys you chose.

In the Cull view, marking a photo as keep or reject moves on to the next one (in Compare, it stays on the same photos). In the Gallery, marks, stars and labels apply to every selected photo.

## Film simulations
Film simulations are 3D LUTs, in the same HaldCLUT format RawTherapee uses (`.png` / `.tif`), or `.cube` files. Tamis does not ship any: choose a folder of them with **Folder…** in the edit panel (sub-folders become groups). RawTherapee's [film simulation collection](https://rawpedia.rawtherapee.com/Film_Simulation) (CC BY-SA) works as is, and gives the same looks as in RawTherapee. The base development of RAWs (LibRaw) is close to, but not identical with, RawTherapee's own.

## Plugins
A plugin is a Python file (or a package folder) in the plugins folder, which **Plugins ▾ > Open Plugins Folder** creates and opens. Plugins load at launch, so restart Tamis after adding one. This is all it takes:

```python
NAME = "Keep the 5-star photos"  # optional: the name shown in the menu

def register(tamis):
    def keep_best():
        best = [photo for photo in tamis.photos() if photo.rating == 5]
        tamis.set_status(best, tamis.KEEP)  # one undo step
        tamis.toast(f"{len(best)} photo(s) kept")

    tamis.add_action("Keep 5-Star Photos", keep_best, shortcut="Ctrl+Shift+J")
    tamis.on("photo_changed", lambda photo: print(photo.path))
```

[`examples/plugins/keeps_list.py`](examples/plugins/keeps_list.py) is a complete example to copy.

| `tamis.` | What it does |
| --- | --- |
| `add_action(title, callback, shortcut=None)` | Adds an entry to the Plugins menu; returns its `QAction`. A shortcut that is invalid or already taken (by the app, with the user's own keys, or another plugin) is dropped and reported. It shows in the `?` overview |
| `on(event, callback)` | Calls `callback` when an event happens (below) |
| `folder`, `photos()`, `current_photo()`, `selected_photos()` | The open folder and its photos. The current photo can be `None`; `selected_photos()` is what a mark would apply to |
| `go_to(photo)` | Shows that photo |
| `set_status(photos, status)` | `tamis.KEEP`, `tamis.REJECT` or `None` |
| `set_rating(photos, stars)`, `set_label(photos, label)` | 0–5 stars; a colour label (`xmp.LABELS`) or `None` |
| `set_edit(photos, edit)` | Replaces the edit with the settings of `develop.DEFAULTS`; `{}` removes it |
| `toast(text)`, `message(title, text, details=None)` | A short message, or a dialog |
| `get_setting(key, default=None)`, `set_setting(key, value)` | Values kept across launches, private to the plugin; anything JSON can represent |
| `version` | Tamis's version, such as `"0.1.0"`, to check compatibility (`"unknown"` when run from a checkout that was never installed) |
| `window` | The main window, for anything else. It is not a stable interface: it may change between versions |

`photos` is a photo or a path, or a list of them, and every `set_…` call is one undo step. A photo is a read-only snapshot with `path`, `name`, `files` (with its companions), `status`, `location`, `rating`, `label`, `edit` and `metadata` (its `title`, `caption` and `keywords`, those that are set), `file` and `copy`. A virtual copy (`copy` 1, 2…) is a photo of its own on another's files: its `path` identifies it but isn't a file (it is the file's path, a NUL character and its number), so read `file` instead; for any other photo, `file` is `path` and `copy` is 0.

| Event | Arguments | When |
| --- | --- | --- |
| `folder_opened` / `folder_refreshed` | `folder` | A folder was opened (once it is read) / photos were added to or removed from the open one |
| `photo_changed` | `photo` | Another photo became the current one |
| `photos_edited` | `[photos]` | Marks, ratings, labels or edits changed, undo included |
| `files_moved` | `{old path: new path}` | Process Files moved photos. Photos are identified by path, so a plugin keeping data per photo must follow this |
| `photos_exported` | `[paths]` | Photos were written to the export folder |
| `photos_trashed` | `[paths]` | Photos of `_REJECTS` were moved to the Trash |
| `closing` | | The window is closing; changes made now are still saved |

Good to know:
- **Plugins run with your rights**, like any program: they can read and delete your files. Only install those you trust. They are only loaded from the plugins folder, never from a photo folder.
- Everything runs on the window's thread, so slow work in a plugin freezes the window: use a `QThread` for it.
- A failing plugin doesn't stop the app: the error appears in a toast and in **Plugins ▾ > Show Problems…**. **Plugins ▾ > Installed** turns a plugin off or on, from the next launch.
- In the built `Tamis.app`, plugins can import `tamis`, numpy, PyQt6's `QtCore`, `QtGui` and `QtWidgets`, and the parts of the standard library the app itself uses. A plugin that needs more runs from source (`python -m tamis`) or adds it to `scripts/build_app.sh` with `--hidden-import`.

## Sessions
`tamis_session.json` stores marks keyed by path relative to the photo folder (e.g. `_KEEPS/IMG_1.CR2`), so the folder can be moved or renamed without losing decisions. Older sessions keyed by filename are still read, and so are `cullspeed_session.json` files from when the app was called CullSpeed; the next save replaces them. Ratings and labels are stored there too; when a photo has an XMP sidecar, its rating and label are read from the sidecar instead, so changes made in another editor win. If a rating can't be written to its sidecar, the session's value wins until a later save manages to write it.

Marks of photos whose file is missing are kept, so a `_KEEPS` taken out of the folder and brought back gets them back. A session file that can't be read is never overwritten: it is renamed `tamis_session.corrupt.json`, the app says so, and the next most recent session is used if there is one.

An edit's history and snapshots are stored in the photo's entry too (`history`, `snapshots`), and so are its virtual copies (`copies`, by number), each with its own marks and edit.

If the photo folder is read-only (locked SD card, read-only share), marks are saved in the app's own data folder instead and reloaded from there when the folder is reopened.

## Code Layout
| Module | Role |
| --- | --- |
| `tamis/logs.py` | The log file, and where native crashes are written |
| `tamis/main.py` | Application entry point |
| `tamis/migration.py` | Brings over settings and sessions from when the app was called CullSpeed |
| `tamis/app.py` | Main window: its UI, showing photos, marking, the Gallery, sorting and analysis |
| `tamis/folders.py` | Opening, reading and refreshing the folder, saving its session and sidecars (part of the main window) |
| `tamis/navigation.py` | Stepping through photos, "Keeps only" and Compare (part of the main window) |
| `tamis/editui.py` | The edit panel, presets, rotation, crop and before/after (part of the main window) |
| `tamis/processing.py` | Exporting, Process Files, importing and the Trash, behind a progress dialog (part of the main window) |
| `tamis/scanning.py` | Reading folders in the background, whole or just what changed |
| `tamis/workers.py` | Helpers shared by the background engines |
| `tamis/models.py` | Photo list model, gallery filter, folder scan and session file |
| `tamis/loader.py` | Background preview/thumbnail loading, caching and prefetching |
| `tamis/thumbcache.py` | Thumbnails on disk |
| `tamis/analysis.py` | Background capture dates, sharpness scores, perceptual hashes and faces, remembered on disk |
| `tamis/faces.py` | Faces with Apple's Vision (macOS): closed eyes, face sharpness, where a face is once framed |
| `tamis/bursts.py` | Grouping shots taken in quick succession |
| `tamis/peaking.py` | Focus peaking overlay and its background worker |
| `tamis/imaging.py` | Thread-safe decoding of RAW and standard images (QImage only) |
| `tamis/exif.py` | Minimal EXIF reader for shooting info, and the EXIF of exported JPEGs |
| `tamis/xmp.py` | Reading and editing ratings, labels, titles, captions and keywords in XMP sidecars |
| `tamis/develop.py` | Edit pipeline (numpy): turns and straightening, exposure, tone, colour, LUT, grain, vignette |
| `tamis/cropping.py` | Geometry of the crop frame while it is dragged |
| `tamis/lut.py` | HaldCLUT / `.cube` loading and fast 256³ lookup tables |
| `tamis/editing.py` | Developing photos for editing, background previews, full-resolution renders and thumbnails of edits, export |
| `tamis/editpanel.py` | The edit panel (Basic and Advanced tabs) and its histogram |
| `tamis/editwidgets.py` | Widgets of the Advanced tab: folding sections, the tone curve editor, the colour wheel |
| `tamis/advancedui.py` | The Advanced tab from the main window: tools on the photo, masks, spots, auto settings, lens and colour profiles, history, snapshots, virtual copies (part of the main window) |
| `tamis/edittools.py` | Tools that work on the photo in the Cull view: white balance picker, colour target, gradients, brush, spots |
| `tamis/curves.py` | Tone curves: monotone point curves and the parametric curve |
| `tamis/colours.py` | Vibrance, the HSL mixer, black and white and colour grading |
| `tamis/local.py` | Settings that read a pixel's neighbourhood: noise reduction, dehaze, texture, clarity, sharpening, defringe |
| `tamis/masks.py` | Local adjustments: masks, where they lie on the picture, and their weights |
| `tamis/automasks.py` | Automatic masks of the subject, people (Vision) and sky, kept on disk |
| `tamis/spots.py` | Spot removal: healing and cloning, and finding a source |
| `tamis/lenses.py` | Lens profiles from lensfun's XML database, and correcting distortion, vignetting and chromatic aberration |
| `tamis/profiles.py` | Camera colour profiles for RAWs: built-in ones and DCPs |
| `tamis/autoadjust.py` | Automatic white balance and tone |
| `tamis/tones.py` | Histograms and clipping overlays, and their background worker |
| `tamis/presets.py` | Named looks, kept in the app's data folder |
| `tamis/exportsettings.py` | Export format, size, quality, folder, name pattern, watermark, credits and profiles |
| `tamis/watermark.py` | Painting a text or logo watermark on exports |
| `tamis/stego.py` | The hidden mark: a short text written invisibly into the pixels, and read back |
| `tamis/metadata.py` | Titles, captions, keywords and credits as XMP and IPTC-IIM in exported JPEGs |
| `tamis/keeplists.py` | Lists of keeps for other editors (Lightroom, one per line, CSV) |
| `tamis/updates.py` | Asking GitHub whether a newer version is out |
| `tamis/fileops.py` | Planning and executing file moves, exports, the Trash and imports |
| `tamis/shortcuts.py` | The table of keyboard shortcuts: defaults, the user's changes, conflicts |
| `tamis/shortcutsdialog.py` | The shortcut overview (`?`) and the editor |
| `tamis/plugins.py` | Loading plugins, the API they receive and the Plugins menu |
| `tamis/views.py` | The Cull tab's photo view (zoom, crop, overlays) and Compare's views |
| `tamis/widgets.py` | Thumbnail delegate, bars, toast and dialogs |
| `tamis/theme.py` | Dark palette and stylesheet |

## Development
```bash
pip install -e ".[dev]"
pytest                     # main window, plugins, edits, crop, presets, tones, LUTs, RAWs, file moves, sessions, XMP, EXIF
pytest --cov=tamis --cov-report=term-missing   # and what the tests don't reach
ruff check .
```
CI runs both on every push and pull request (`.github/workflows/ci.yml`), on macOS, Linux and Windows. The RAW tests use tiny DNGs written on the fly (`tests/rawfiles.py`), so no camera file is shipped.

To build a standalone app (`dist/Tamis.app` on macOS, `dist/Tamis/` elsewhere):
```bash
pip install -e ".[build]"
./scripts/build_app.sh
```
Pull requests into `master` also build it on Linux and Windows (`.github/workflows/build.yml`, or by hand from the Actions tab); each build is attached to the run as an artifact for 14 days. These builds are unsigned and only meant for trying a change there.

Measuring:
```bash
python scripts/bench_render.py                       # develop.render() at preview and 24 Mpx sizes: time and peak memory
python scripts/startup_time.py <folder of photos>    # launch to first preview, from source and dist/Tamis.app
python scripts/sharpness_report.py <culled folder>   # sharpness scores against your marks, to tune the thresholds
```
`startup_time.py` runs the app with `TAMIS_STARTUP_TIME` set to the folder: it opens it with settings and caches of its own, prints when the first preview is shown and quits. `sharpness_report.py` writes a CSV, a contact sheet sorted by score and a summary into `sharpness_report/<shoot name>` (or `--out`), never into the shoot, and prints how well `soft` would have matched your rejects at several ratios. Each shoot gets its own report: running it again on the same shoot replaces that one, and another shoot of the same name gets `<name> (2)`.

### Releases
`scripts/build_app.sh` gives the bundle the version of `pyproject.toml` (`CFBundleShortVersionString`) and signs it ad hoc. `scripts/package_macos.sh` wraps it in `dist/Tamis-<version>.dmg` and a `.zip`.

Pushing a tag such as `v0.2.0` (matching `pyproject.toml`) runs `.github/workflows/release.yml`, which tests, builds and packages the app and publishes the DMG and zip as a GitHub release with generated notes. Linux and Windows builds (`Tamis-<version>-linux.tar.gz`, `Tamis-<version>-windows.zip`, unsigned) are then added to the same release; if one of them fails, the macOS release stays as it is. No Apple Developer account is needed: the app is signed ad hoc and not notarized, so **macOS blocks it the first time it is opened**. To open it anyway: try once, then **System Settings › Privacy & Security › Open Anyway**; or in Terminal:
```bash
xattr -dr com.apple.quarantine /Applications/Tamis.app
```
The release notes say so too. With an Apple Developer account, setting the repository secrets `MACOS_CERTIFICATE` (the Developer ID Application certificate and key, as a base64 `.p12`), `MACOS_CERTIFICATE_PASSWORD` and `MACOS_SIGN_IDENTITY`, and for notarization `NOTARY_KEY` (an App Store Connect API key, base64 `.p8`), `NOTARY_KEY_ID` and `NOTARY_ISSUER`, makes the same workflow sign the app with that Developer ID (hardened runtime, `scripts/entitlements.plist`) and notarize the DMG; `package_macos.sh`'s header lists the matching environment variables for doing it locally.

Tamis tells its users about new releases by itself (see **New versions** above), from the release's tag.

## Development Notes
- Worker threads only produce `QImage`; `QPixmap` is created on the GUI thread.
- The app uses its own `QDialog`s instead of `QMessageBox`: with Qt 6.11 on recent macOS, `QMessageBox`'s native `NSAlert` crashes AppKit (`trace trap`).
- `faulthandler` is enabled at startup and writes to the log file, so native crashes leave a Python traceback there. Modules log through `logging.getLogger(__name__)`; warnings and errors reach the file.
