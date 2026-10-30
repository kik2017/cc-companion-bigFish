"""Convert the bundled character's clips from dsh-pet's animation videos.

    python tools/import_clips.py --cache DIR               # every clip
    python tools/import_clips.py --cache DIR nap curtsy    # just these

Needs `pip install -e .[clips]`: PyAV, whose FFmpeg has the libvpx decoder that keeps
VP9's alpha channel, and Pillow. Each clip comes from one WebM in PC2005-cloud/dsh-pet at
SOURCE_COMMIT, downloaded into the cache folder when missing (only ever decoded, never
run). Every frame is cut to the same CROP window of the 640 x 360 source, so all clips
line up with each other, and written as lossy WebP with lossless alpha, 24 fps, into
companion/assets/character/<clip>/<clip>_NNN.webp. manifest.json's frameSize and clips
are rewritten to match; its mappings (states, reactions, idle, talk, cues, ...) are kept
as you wrote them.

The frames are the character's art: CC BY-NC-SA 4.0, non-commercial, with the credits in
the folder's LICENSE.txt and SOURCE.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SOURCE_COMMIT = "549fff2ea33c2acfb16612c76912c6137da4a758"
SOURCE_URL = "https://raw.githubusercontent.com/PC2005-cloud/dsh-pet/{commit}/dsh-pet/assets/webm/{name}.webm"
CHARACTER = Path(__file__).resolve().parents[1] / "companion" / "assets" / "character"
CROP = (56, 0, 584, 360)  # wide enough for the widest clip, the money bags
FRAME_MS = 42  # 24 fps
QUALITY, METHOD = 75, 4  # method 6 is a hundred times slower for 2% smaller files

# clip: (source video, first frame, last frame or None for the end, loops)
CLIPS: dict[str, tuple[str, int, int | None, bool]] = {
    # states
    "idle": ("待机呼吸休闲", 0, None, True),
    "thinking": ("工作状态-思考冒泡", 0, None, True),
    "deep_thought": ("深度思考碎碎念", 0, None, True),
    "look_around": ("东张西望", 0, None, True),
    "take_notes": ("轻快记录", 0, None, True),
    "working": ("工作状态-忙碌点按", 0, None, True),
    "write_code": ("写代码", 0, None, True),
    "desk_tap": ("原地敲击桌面互动", 0, None, True),
    "working_command": ("工作状态-清点归档", 0, None, True),
    "waiting": ("工作状态-原地踱步张望", 0, None, True),
    "success": ("工作状态-雀跃庆祝", 0, None, False),
    "error": ("工作状态-垂头叹气冒汗", 0, None, False),
    # reactions
    "head_pat": ("点击回应-挠痒咯咯笑", 0, None, False),
    "shy": ("点击回应-害羞惊讶", 0, None, False),
    "poke": ("点击回应-元气挥手", 0, None, False),
    "huff": ("点击回应-傲娇生气", 0, None, False),
    "happy_hop": ("点击回应-开心跃动", 0, None, False),
    "tail": ("鲸鱼吐泡泡特效", 0, None, False),
    "tail_slap": ("用鲸鱼尾巴拍打地面", 0, None, False),
    "dragging": ("被鼠标拖拽悬空反馈", 48, 191, True),
    "dragging_release": ("被鼠标拖拽悬空反馈", 192, 240, False),
    "startled": ("被吓一跳", 0, None, False),
    # idle, talking and cues
    "idle_stretch": ("超大伸懒腰", 0, None, False),
    "idle_yawn": ("哈欠连天", 0, None, False),
    "eat_token": ("吃Token", 0, None, False),
    "hum": ("悠闲哼歌", 0, None, False),
    "chatter_daydream": ("碎碎念-发呆碎碎念", 0, None, False),
    "chatter_screen": ("碎碎念-对屏碎碎念", 0, None, False),
    "chatter_wipe": ("碎碎念-擦桌碎碎念", 0, None, False),
    "curtsy": ("女仆屈膝礼仪", 0, None, False),
    "nap": ("原地小憩沉眠", 0, None, False),
    "doze": ("打瞌睡被惊醒", 0, None, False),
    # times of day
    "brush_teeth": ("晨间刷牙", 0, None, False),
    "breakfast": ("吃早餐", 0, None, False),
    "lunch": ("吃午餐", 0, None, False),
    "dinner": ("吃晚餐", 0, None, False),
    # festivals (the fireworks' outermost sparks go past the window and are cut)
    "pumpkin": ("讨糖南瓜灯", 0, None, False),
    "ghost": ("萌化小幽灵", 0, None, False),
    "xmas_tree": ("装点圣诞树", 0, None, False),
    "fireworks": ("放烟花", 0, None, False),
    "write_fu": ("写福字", 0, None, False),
    "red_envelope": ("收红包", 0, None, False),
    "lion_dance": ("舞狮头", 0, None, False),
    "tangyuan": ("吃汤圆", 0, None, False),
    "qingtuan": ("吃青团", 0, None, False),
    "zongzi": ("吃粽子", 0, None, False),
    "qixi": ("穿针乞巧", 0, None, False),
    "mooncake": ("中秋赏月吃月饼", 0, None, False),
    "double_ninth": ("吃重阳糕", 0, None, False),
    "dumplings": ("吃饺子", 0, None, False),
    "laba": ("吃腊八粥", 0, None, False),
    # usage, from plenty to none
    "purse_full": ("余额-钱袋满溢", 0, None, False),
    "purse_jingle": ("余额-金袋叮当", 0, None, False),
    "purse_steady": ("余额-钱袋如常", 0, None, False),
    "purse_frown": ("余额-数金皱眉", 0, None, False),
    "purse_empty": ("余额-袋空如洗", 0, None, False),
    "purse_broke": ("余额-分文不剩", 0, None, False),
}
# Clips that reuse another clip's frames, played once (a looping state clip as an idle one).
ONCE: dict[str, str] = {"look_around_once": "look_around", "take_notes_once": "take_notes"}


def fetch(cache: Path, source: str) -> Path:
    path = cache / f"{source}.webm"
    if not path.exists():
        url = SOURCE_URL.format(commit=SOURCE_COMMIT, name=urllib.parse.quote(source))
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        path.write_bytes(data)
    return path


def frames(path: Path):
    """Every frame of a VP9 WebM as RGBA, transparency kept."""
    import av
    from PIL import Image

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        decoder = av.CodecContext.create("libvpx-vp9", "r")  # the built-in vp9 decoder drops alpha
        for packet in container.demux(stream):
            for frame in decoder.decode(packet):
                rgba = frame.reformat(format="rgba")
                plane = rgba.planes[0]
                yield Image.frombuffer("RGBA", (rgba.width, rgba.height), bytes(plane), "raw", "RGBA", plane.line_size, 1)


def convert(name: str, cache: Path, out: Path) -> int:
    source, first, last, _loop = CLIPS[name]
    folder = out / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    count = 0
    for index, image in enumerate(frames(fetch(cache, source))):
        if index < first or (last is not None and index > last):
            continue
        count += 1
        image.crop(CROP).save(folder / f"{name}_{count:03d}.webp", "WEBP", quality=QUALITY, method=METHOD, alpha_quality=100)
    if not count:
        raise SystemExit(f"{name}: no frames in {source}")
    return count


def write_manifest(out: Path) -> None:
    path = out / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["frameSize"] = [CROP[2] - CROP[0], CROP[3] - CROP[1]]
    clips = {name: {"dir": name, "frameMs": FRAME_MS, "loop": loop} for name, (_s, _f, _l, loop) in CLIPS.items()}
    clips.update({name: {"dir": base, "frameMs": FRAME_MS, "loop": False} for name, base in ONCE.items()})
    manifest["clips"] = clips
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cache", type=Path, required=True, help="folder for the downloaded WebM files")
    parser.add_argument("--out", type=Path, default=CHARACTER, help="the character folder (default: the bundled one)")
    parser.add_argument("clips", nargs="*", help="clip names to convert (default: all)")
    args = parser.parse_args()
    unknown = [c for c in args.clips if c not in CLIPS]
    if unknown:
        parser.error(f"unknown clip(s): {', '.join(unknown)}")
    args.cache.mkdir(parents=True, exist_ok=True)
    for name in args.clips or CLIPS:
        print(f"{name}: {convert(name, args.cache, args.out)} frames", flush=True)
    write_manifest(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
