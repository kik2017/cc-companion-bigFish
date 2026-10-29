# The bundled character

The whale-tail maid, animated by [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet). The character design is by Bilibili @ZipZipPipe, based on the original character 溟月 by Bilibili @上善无形.

**This art is not MIT.** dsh-pet's MIT license covers its code only. Its art is for open-source, non-commercial use, and the character is licensed CC BY-NC-SA 4.0. These frames are shared under CC BY-NC-SA 4.0, non-commercial only. `LICENSE.txt` has the full terms and credits, and must stay with these files.

- Source: the dsh-pet animation videos at commit `549fff2ea33c2acfb16612c76912c6137da4a758`, `dsh-pet/assets/webm`
- Changes: decoded with their transparency, cut to one shared 528 x 360 window of the 640 x 360 videos (x 56-584), and saved as transparent WebP at 24 fps (42 ms per frame); 42 clips, 9,710 frames. The drag clip is frames 48-191 of the dragged-in-the-air video, and its landing frames 192-240.
- `tools/import_clips.py` in the repo rebuilds these frames from the source videos and lists which video each clip comes from
- `manifest.json` maps cc-companion's states, reactions, idle clips, moods, talking, cues, times of day, usage tiers and hit zones onto those clips

Names and project identity belong to their owners.
