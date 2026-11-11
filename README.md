# Sharewise

**See what your photos and videos reveal before you share them.** A photo or video from a phone usually carries the exact place it was
taken (often to a few metres, with altitude and the direction you faced), the exact time, the phone model, unique
IDs, sometimes a hidden thumbnail of the original and a depth map. Sharewise tells you, in plain words, without ever
reading those values out; it finds the faces of strangers, street names, house numbers and licence plates; you
decide what to hide; it hides them for good and gives you verified clean copies. Originals are never changed.

```text
You:     I want to post the Singapore photos in ~/Pictures/Singapore. Are they safe?
Hermes:  Checked exactly ~/Pictures/Singapore: 5 photos. All 5 store the exact place they were taken, the exact
         time, the phone model and unique IDs; 3 also hide a thumbnail of the original and a depth map.
         Want me to look for faces and street signs? You'll get a page to review each photo.
You:     Yes. [opens the page, switches off their own face, draws one more area, copies the code]
You:     SW1.3f9a1c2e.3.1,2.050495075050.h6n
Hermes:  Made 5 copies in ~/Pictures/Singapore/Sharewise: 9 areas hidden, every copy verified with no location,
         no hidden thumbnails, no IDs. Share those, not the originals.
```

## How it works

1. **Check** one folder you name. You get yes/no counts: location, location detail, time, device, unique IDs,
   owner name, captions, hidden thumbnails, editing history, depth maps. Videos (MOV, MP4, M4V, 3GP) are checked for
   location, dates and device.
2. **Review** (macOS): Apple's Vision framework, on this computer, finds faces (on the whole photo and again on
   overlapping high-resolution tiles, so small faces in a crowd are found too) and text that can locate someone:
   street names, house or unit numbers, licence plates. People with no face found (often facing away) and other text
   (slides, shop signs) are proposed switched off. A local review page shows each photo **before and after**; click an
   area to switch it off or on, drag to add your own, and copy the selection code back to Hermes.
3. **Share**: copies go to a `Sharewise/` folder next to the photos. Every copy is re-read and verified. Video copies
   are not re-encoded: picture and sound samples stay byte-identical and the file keeps its size. Inside the copy,
   Sharewise blanks the movie and track metadata (location, accuracy, dates, make, model, software, lens, maker
   notes), drops timed-metadata tracks (iPhone Live Photo videos record per-frame **face positions and IDs** there),
   zeroes every byte of the media area that no picture or sound track uses (some phones leave a stray copy of the
   header there, location included), zeroes padding boxes (which can hold an old header), and sets header dates to
   zero. The finished copy is then scanned byte by byte, not just box by box. If a video's structure is unusual
   (sample tables unreadable, or more than 3% of the media area unreferenced), it is refused rather than risked.
4. **Visual second look** (optional): Hermes can look at the finished copies, never the originals, for anything
   detection missed.

## Hidden for good

Blur and pixelation keep a transformed copy of what was there and can sometimes be reversed or recognised.
Faces (ovals): Sharewise never looks at the pixels inside the oval when it fills it: they are discarded first, and the
patch is rebuilt only from the surroundings (weighted towards visible skin on the neck and ears, so it blends in), with
fresh random grain. The tests prove it: changing what was inside an oval does not change a single output pixel.
Signs, plates and house numbers (rectangles, rotated to follow the sign): the area becomes one flat colour, the sign's
own plate colour, with fresh grain, so the sign stays a sign with nothing written on it. Writing lives in the strokes,
not in the plate colour; the tests check that no fine detail is left. On the review page every area can be moved,
resized, rotated and switched between oval and rectangle.
The copy also drops hidden thumbnails and depth maps, which often still contain the original face.

## Walls (enforced in code and tested)

- **No values ever leave Sharewise**: no coordinates, times, devices, IDs, names, comments, and no words read in a
  photo: not in answers to Hermes, not in its reports, not in its database. Only yes/no and area categories.
- **Remove-only**: Sharewise never adds or edits metadata. Copyright and licence notices are kept.
- **Originals are never written**: copies are created new, never overwriting anything; plain JPEG cleaning is lossless.
- **Verified copies**: a copy that still reveals something you did not choose to keep is deleted and reported.
  Videos are checked with a whole-file scan as well, because metadata can hide outside the official boxes.
- **Location is removed by default**; keeping it shows Hermes' approval prompt.
- **The agent passes check ids, photo numbers and fixed choices only**, never file paths. It may **add** areas to
  hide; it can never switch one off. Switching off comes from the review page's selection code. (This is a design
  rule against mistakes, not a cryptographic barrier against an agent that can read files.)
- **Allowed places only**: folders inside your home folder or on external drives; the operating system, app data,
  hidden, locked and library folders are refused before anything is read.
- **Not a tracking tool**: because no location or text is ever revealed, Sharewise cannot be used to find out where
  someone else's photo was taken.

## Privacy and disclosure

- **Reads** photos (JPEG, HEIC/HEIF, PNG, WebP) and videos (MOV, MP4, M4V, 3GP) in the folder you name, within the
  allowed places.
- **Writes** only a `Sharewise/` folder inside that folder, with new copies (`-clean` or `-shared` names). Replacing
  happens only for copies the same check made earlier.
- **Stores** in Hermes' plugin data folder: the last 10 checks (file paths, yes/no findings, area boxes), review pages
  with downsized photos, and downsized finished copies for the visual check. No metadata values, no text from photos.
- **Sent to your model provider** as part of the conversation: the folder, photo names, yes/no counts and area counts;
  with `sharewise_look`, downsized finished copies (already hidden and clean), only when you ask for a visual check.
- **Detection** runs on-device with Apple Vision (macOS, `pyobjc-framework-Vision`). On Linux and Windows it is not
  available: checking, drawing areas by hand and clean copies still work.
- No network calls, model calls, shell commands, background processes or telemetry. No process-wide settings changed.

## Limitations

- Detection is not perfect: it can miss small, turned or covered faces and blurry text, and can mark things that are
  not faces. Always look at the review page.
- Hiding a face does not make a person anonymous to someone who knows them (clothes, hair, posture, place).
- HEIC is converted to JPEG for sharing; photos with hidden areas are re-encoded (JPEG quality 92).
- Faces and signs **inside videos are not hidden**: videos get metadata cleaning only. Keep options (time, device,
  location) apply to photos; video copies always drop all of them.
- RAW files, TIFF and other video containers (AVI, MKV, WebM) are not cleaned (they can carry location too);
  Sharewise lists them as not checked.

## Tools

| Tool | What it does | Changes files? |
|---|---|---|
| `sharewise_check` | Yes/no report of what the photos and videos in one folder reveal | No |
| `sharewise_review` | Detects areas; builds a before/after review page per photo | No (pages in its data folder) |
| `sharewise_share` | Hides the chosen areas, removes metadata, verifies, saves copies to `Sharewise/` | New copies only |
| `sharewise_look` | Downsized finished copies for Hermes' visual check | No |

## Development

```bash
python -m pytest
hermes plugins validate . --install-deps
hermes plugins doctor . --ci
```

## License

MIT
