"""Build docs/demo.gif from real output: the example sheet, `tick`, a reminder, the link pages.

    uv run python scripts/make_demo.py      (needs Google Chrome and ffmpeg)
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from html import escape
from pathlib import Path

import httpx
from rich.console import Console
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import make_example

from ghostbooking import cli
from ghostbooking.config import Links, load_config
from ghostbooking.engine import Engine
from ghostbooking.messages import reminder_message
from ghostbooking.notify.message import bold
from ghostbooking.rules import plan_reset
from ghostbooking.sources import open_source
from ghostbooking.web import make_server

W, H = 1200, 675
GIF_FILTER = ("fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];"
              "[b][p]paletteuse=dither=bayer:bayer_scale=4")
MONDAY = datetime(2026, 11, 23, 7, 30)
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"

FRAME = """<!doctype html><html><head><meta charset="utf-8"><style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; width: {w}px; height: {h}px; overflow: hidden; background: #eef1f6; color: #1d2330;
          font: 18px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; display: flex;
          flex-direction: column; padding: 36px 48px; }}
  .cap {{ font-size: 15px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: #2456c8; }}
  h1 {{ font-size: 30px; margin: 4px 0 20px; }}
  .stage {{ flex: 1; display: flex; align-items: flex-start; justify-content: center; min-height: 0; }}
  table {{ border-collapse: collapse; background: #fff; font-size: 15px; box-shadow: 0 2px 10px rgb(0 0 0 / .08); }}
  th {{ background: #2456c8; color: #fff; padding: 6px 14px; text-align: left; }}
  td {{ padding: 5px 14px; border-bottom: 1px solid #e3e7ee; white-space: nowrap; }}
  td.n {{ color: #8a93a3; background: #f6f7f9; text-align: right; }}
  tr.ghost td {{ background: #fde4e1; text-decoration: line-through; color: #8c2a20; }}
  tr.clash td {{ background: #fff3cd; }}
  tr.bad td {{ background: #f3e5f5; }}
  .tag {{ font-size: 12px; font-weight: 700; padding: 1px 8px; border-radius: 99px; text-decoration: none;
          display: inline-block; }}
  .tag.ghost {{ background: #b3261e; color: #fff; }} .tag.clash {{ background: #8a6100; color: #fff; }}
  .tag.bad {{ background: #6a1b9a; color: #fff; }} .tag.keep {{ background: #0b6e4f; color: #fff; }}
  pre {{ margin: 0; background: #10141b; color: #d8dee9; padding: 22px 26px; border-radius: 12px; font-size: 15px;
         line-height: 1.5; width: 100%; overflow: hidden; font-family: "DejaVu Sans Mono", Menlo, monospace; }}
  .chat {{ background: #fff; border-radius: 12px; width: 760px; box-shadow: 0 2px 10px rgb(0 0 0 / .08); }}
  .chat header {{ padding: 12px 20px; border-bottom: 1px solid #e3e7ee; font-weight: 700; }}
  .msg {{ display: flex; gap: 14px; padding: 20px; }}
  .avatar {{ width: 44px; height: 44px; border-radius: 10px; background: #2456c8; color: #fff; font-size: 24px;
             display: grid; place-items: center; flex: none; }}
  .msg b {{ display: block; }} .msg p {{ margin: 4px 0 14px; }}
  .btn {{ display: inline-block; padding: 8px 18px; border-radius: 8px; border: 1px solid #c9d0db;
          font-weight: 600; margin-right: 8px; }}
  .btn.primary {{ background: #2456c8; color: #fff; border-color: #2456c8; }}
  .phone {{ width: 420px; height: 470px; border-radius: 28px; overflow: hidden; border: 10px solid #1d2330;
            background: #fff; }}
  .phone iframe {{ width: 100%; height: 100%; border: 0; }}
  .pair {{ display: flex; gap: 48px; }}
  .title {{ flex: 1; display: flex; flex-direction: column; justify-content: center; align-items: center;
            text-align: center; }}
  .title .ghost {{ font-size: 96px; }} .title h1 {{ font-size: 46px; margin: 8px 0; }}
  .title p {{ font-size: 24px; color: #5d6675; margin: 0; }}
</style></head><body>{body}</body></html>"""


def frame(body: str) -> str:
    return FRAME.format(w=W, h=H, body=body)


def scene(cap: str, title: str, stage: str) -> str:
    return frame(f'<div class="cap">{escape(cap)}</div><h1>{escape(title)}</h1><div class="stage">{stage}</div>')


def sheet_table(path: Path, cfg, marks: dict[int, str]) -> str:
    from openpyxl import load_workbook

    ws = load_workbook(path).active
    rows = list(ws.iter_rows(values_only=True))
    labels = {"ghost": "ghost", "clash": "double-booked", "bad": "can't read", "keep": "kept"}
    out = ["<table><tr><th></th>" + "".join(f"<th>{escape(str(h))}</th>" for h in rows[0]) + "<th></th></tr>"]
    for i, row in enumerate(rows[1:], start=2):
        if all(v is None for v in row):
            continue
        cells = []
        for v in row:
            if isinstance(v, datetime):
                v = v.strftime("%d/%m/%Y")
            elif hasattr(v, "strftime"):
                v = v.strftime("%H:%M")
            cells.append(f"<td>{escape('' if v is None else str(v))}</td>")
        mark = marks.get(i, "")
        tag = f'<span class="tag {mark}">{labels[mark]}</span>' if mark else ""
        out.append(f'<tr class="{mark}"><td class="n">{i}</td>{"".join(cells)}<td>{tag}</td></tr>')
    return "".join(out) + "</table>"


def terminal(argv: list[str], shown: str, hide: str = "") -> str:
    rec = Console(record=True, width=104, force_terminal=True, soft_wrap=True, file=io.StringIO())
    cli.console = rec
    cli.main(argv)
    lines = rec.export_text(styles=True).replace(hide, "").splitlines()
    text = Text.from_ansi("\n".join([f"$ {shown}", *lines]))
    html = Console(record=True, width=104, file=io.StringIO())
    html.print(text)
    body = html.export_html(inline_styles=True, code_format="{code}")
    return f"<pre>{body}</pre>"


def main() -> None:
    out = ROOT / "docs" / "demo.gif"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        sheet = tmp / "room-bookings.xlsx"
        make_example.build(sheet)
        cfg_text = (ROOT / "examples" / "config.yaml").read_text(encoding="utf-8")
        cfg_text = cfg_text.replace("  - type: console\n", "  []\n").replace("channels:\n  []", "channels: []")
        (tmp / "config.yaml").write_text(cfg_text, encoding="utf-8")
        cfg = load_config(tmp / "config.yaml")

        # 1. Monday morning, before.
        plan = plan_reset(open_source(cfg.source, cfg.backups).read(), cfg, MONDAY)
        marks = {r.booking.row: "ghost" for r in plan.removals}
        marks.update({c.clashing.row: "clash" for c in plan.conflicts})
        marks.update({i.row: "bad" for i in plan.issues})
        marks.update({b.row: "keep" for b in plan.protected})
        frames = [frame('<div class="title"><div class="ghost">👻</div><h1>Ghostbooking</h1>'
                        "<p>For startups that still book meeting rooms in a shared Excel file.</p></div>")]
        frames.append(scene("Monday 07:30", "Friday's meetings are still in the sheet",
                            sheet_table(sheet, cfg, marks)))
        # 2. The morning reset, for real.
        term = terminal(["tick", "-c", str(tmp / "config.yaml"), "--now", "2026-11-23 07:30"],
                        'ghostbooking tick --now "2026-11-23 07:30"', hide=f"{tmp}/")
        frames.append(scene("ghostbooking tick", "The morning reset clears them, with a backup",
                            term))
        frames.append(scene("Monday 07:31", "A clean sheet, formatting intact", sheet_table(sheet, cfg, {})))
        # 3. A reminder with Keep / Release links, and the pages they open.
        cfg.links = Links(base_url="https://rooms.example.com", secret="demo-secret-0123456789",
                          listen="127.0.0.1:0")
        engine = Engine(cfg)
        sh = engine.source().read()
        ines = next(b for b in sh.bookings if b.booked_by == "Ines")
        msg = reminder_message(ines, ines.day, datetime(2026, 11, 23, 13, 50), cfg)
        chat = ('<div class="chat"><header>Direct message</header><div class="msg">'
                '<div class="avatar">👻</div><div><b>Ghostbooking <span style="color:#8a93a3;font-weight:400">'
                f"13:50</span></b><p>{bold(escape(msg.text), "<strong>", "</strong>").replace(chr(10), "<br>")}</p>"
                '<span class="btn primary">Keep it</span><span class="btn">Release the room</span>'
                "</div></div></div>")
        frames.append(scene("13:50, on Slack, Teams, Discord or email", "A reminder before the meeting", chat))
        server = make_server(engine, lambda: datetime(2026, 11, 23, 13, 51))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        token = msg.links[1].url.rsplit("/r/", 1)[1]
        url = f"http://127.0.0.1:{server.server_address[1]}/r/{token}"
        confirm = httpx.get(url).text
        done = httpx.post(url).text
        server.shutdown()
        (tmp / "confirm.html").write_text(confirm, encoding="utf-8")
        (tmp / "done.html").write_text(done, encoding="utf-8")
        frames.append(scene("One tap", "Release the room, and the sheet updates itself",
                            '<div class="pair"><div class="phone"><iframe src="confirm.html"></iframe></div>'
                            '<div class="phone"><iframe src="done.html"></iframe></div></div>'))
        frames.append(frame('<div class="title"><div class="ghost">👻</div><h1>No more ghost bookings</h1>'
                            "<p>Open source, MIT · Excel, CSV or Google Sheets · "
                            "Slack, Teams, Discord, email, webhooks</p>"
                            '<p style="margin-top:18px;font-weight:600;color:#2456c8">'
                            "github.com/MedAmine-SUDO/ghostbooking</p></div>"))

        pngs = []
        for i, html in enumerate(frames):
            page = tmp / f"frame{i}.html"
            page.write_text(html, encoding="utf-8")
            png = tmp / f"frame{i}.png"
            subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                            "--force-color-profile=srgb", "--allow-file-access-from-files",
                            f"--window-size={W},{H}", f"--screenshot={png}", page.as_uri()],
                           check=True, capture_output=True)
            pngs.append(png)
            if os.environ.get("KEEP_FRAMES"):  # to look at single frames
                shutil.copy(png, out.parent / f"frame{i}.png")
        durations = [2.5, 4.5, 5, 3.5, 4.5, 4.5, 3]
        concat = tmp / "frames.txt"
        concat.write_text("".join(f"file '{p}'\nduration {d}\n" for p, d in zip(pngs, durations))
                          + f"file '{pngs[-1]}'\n", encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                        "-vf", GIF_FILTER, str(out)], check=True)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
