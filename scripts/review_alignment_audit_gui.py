#!/usr/bin/env python3
"""Tkinter desktop client for the local alignment-review server.

Runs on the reviewer's own machine (Windows supported with the standard
library only) and talks to ``scripts/review_alignment_audit.py serve``
through an SSH tunnel such as::

    ssh -L 8765:127.0.0.1:8765 <account>@<server>

The client only ever sees namespaced SHA-256 item hashes and per-item WAV
payloads streamed for playback. Raw utterance IDs and restricted locator
paths never leave the server. Temporary audio files are written to a
private directory and removed when the app exits.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, simpledialog, ttk
except ImportError:  # headless machine; the GUI needs a desktop Python
    tk = None  # type: ignore[assignment]

# Pre-selected so the reviewer only has to change rows they disagree with.
# Trade-off: an item left untouched is indistinguishable from one actually
# listened to and judged correct, which weakens the audit's evidentiary
# value versus requiring an explicit choice per item. No current tracked guide
# authorizes treating this default as a human decision, so untouched rows must
# remain evidentially unreviewed.
DEFAULT_ALIGNMENT_CORRECT = "correct"
DEFAULT_MAXIMUM_BOUNDARY_ERROR_MS = "0"
DEFAULT_INCORRECT_INTERVAL_COUNT = "0"
ANNOTATION_FIDELITY_VALUES = (
    "consistent",
    "target_annotation_mismatch",
    "realized_annotation_mismatch",
    "both_annotations_mismatch",
    "uncertain",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8765",
        help=(
            "review server URL; the full address printed by the server "
            "(including #token=...) may be pasted here"
        ),
    )
    parser.add_argument(
        "--token",
        default=None,
        help=(
            "review access token; defaults to the #token= fragment of "
            "--url or the VOICE_COACH_REVIEW_TOKEN environment variable"
        ),
    )
    return parser.parse_args()


def split_url_and_token(url: str, token: str | None) -> tuple[str, str]:
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if token is None and parts.fragment.startswith("token="):
        token = parts.fragment[len("token=") :]
    if token is None:
        token = os.environ.get("VOICE_COACH_REVIEW_TOKEN")
    if parts.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit(
            "refusing non-loopback server URL; connect through an SSH "
            "tunnel so the review server stays local to the data machine"
        )
    return base, token or ""


class ReviewClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(
        self, path: str, *, method: str = "GET", body: dict[str, Any] | None = None
    ) -> Any:
        data = None
        headers = {"X-Review-Token": self.token}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def status(self) -> dict[str, Any]:
        return self._request("/api/status")

    def items(self) -> list[dict[str, Any]]:
        return self._request("/api/items")

    def item(self, item_hash: str) -> dict[str, Any]:
        return self._request(f"/api/items/{item_hash}")

    def audio_bytes(self, item_hash: str) -> bytes:
        request = urllib.request.Request(
            f"{self.base_url}/api/items/{item_hash}/audio",
            headers={"X-Review-Token": self.token},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()

    def save(
        self, item_hash: str, body: dict[str, Any], *, replace: bool
    ) -> dict[str, Any]:
        return self._request(
            f"/api/items/{item_hash}/review",
            method="PUT" if replace else "POST",
            body=body,
        )


class AudioPlayer:
    """Plays one temporary WAV at a time and cleans up after itself."""

    def __init__(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="alignment-review-"))
        self.current: Path | None = None
        self.payload: bytes | None = None
        self._process: subprocess.Popen[bytes] | None = None
        atexit.register(self.cleanup)

    def load(self, payload: bytes) -> None:
        self.stop()
        self.payload = payload
        if self.current is not None:
            self.current.unlink(missing_ok=True)
        self.current = self.directory / "current.wav"
        self.current.write_bytes(payload)

    def play(self) -> None:
        if self.current is None or self.payload is None:
            return
        self.stop()
        if sys.platform == "win32":
            import winsound

            # SND_MEMORY avoids handing a filesystem path (which can be a
            # Korean-username temp directory) to the underlying Windows
            # PlaySound API; some source WAVs still fail silently (no
            # exception, no audio) on this API depending on their sample
            # format, hence the "기본 플레이어로 열기" fallback button.
            try:
                winsound.PlaySound(
                    self.payload, winsound.SND_MEMORY | winsound.SND_ASYNC
                )
            except RuntimeError:
                winsound.PlaySound(
                    str(self.current),
                    winsound.SND_FILENAME | winsound.SND_ASYNC,
                )
            return
        player = next(
            (
                name
                for name in ("afplay", "paplay", "aplay", "ffplay")
                if shutil.which(name)
            ),
            None,
        )
        if player is None:
            raise RuntimeError("no local WAV player found on this platform")
        command = [player, str(self.current)]
        if player == "ffplay":
            command = [player, "-nodisp", "-autoexit", str(self.current)]
        self._process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def open_externally(self) -> None:
        """Open the current WAV in the OS-default player as a fallback.

        Useful when in-process playback stays silent without raising an
        error, which some source WAV sample formats can trigger on the
        Windows PlaySound API.
        """
        if self.current is None:
            return
        if sys.platform == "win32":
            os.startfile(str(self.current))  # noqa: S606 - local temp WAV only
        else:
            self.play()

    def stop(self) -> None:
        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        elif self._process is not None:
            self._process.terminate()
            self._process = None

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.directory, ignore_errors=True)


class ReviewApp:
    def __init__(self, root: tk.Tk, client: ReviewClient) -> None:
        self.root = root
        self.client = client
        self.player = AudioPlayer()
        self.items: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None

        root.title("Local Alignment Review (tkinter)")
        root.geometry("1180x760")

        banner = tk.Label(
            root,
            text=(
                "로컬 전용 · 기관 인증/TLS 없음 · 외부 공개 금지 · "
                "메모에 개인정보/원본 파일명/경로를 쓰지 마세요"
            ),
            bg="#801c1c",
            fg="white",
            anchor="w",
            padx=10,
            pady=6,
        )
        banner.pack(fill="x")
        self.status_var = tk.StringVar(value="서버에 연결하는 중…")
        tk.Label(root, textvariable=self.status_var, anchor="w", padx=10).pack(fill="x")
        self.error_var = tk.StringVar(value="")
        tk.Label(
            root, textvariable=self.error_var, anchor="w", padx=10, fg="#a11212"
        ).pack(fill="x")

        main = tk.PanedWindow(root, sashrelief="raised")
        main.pack(fill="both", expand=True)

        left = tk.Frame(main)
        self.listbox = tk.Listbox(left, width=34, font=("Consolas", 10))
        scrollbar = tk.Scrollbar(left, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        main.add(left)

        right = tk.Frame(main, padx=10, pady=6)
        main.add(right)
        self.title_var = tk.StringVar(value="항목을 선택하세요")
        tk.Label(
            right, textvariable=self.title_var, font=("", 12, "bold"), anchor="w"
        ).pack(fill="x")

        controls = tk.Frame(right)
        controls.pack(fill="x", pady=4)
        tk.Button(controls, text="▶ 재생", command=self.on_play).pack(side="left")
        tk.Button(controls, text="■ 정지", command=self.player.stop).pack(
            side="left", padx=6
        )
        tk.Button(
            controls, text="🔊 기본 플레이어로 열기", command=self.on_open_externally
        ).pack(side="left", padx=6)

        self.detail = scrolledtext.ScrolledText(right, height=18, wrap="none")
        self.detail.pack(fill="both", expand=True)
        self.detail.configure(state="disabled")

        form = tk.Frame(right)
        form.pack(fill="x", pady=6)
        tk.Label(form, text="정렬 정확성").grid(row=0, column=0, sticky="w")
        self.correct_var = tk.StringVar(value="")
        ttk.Combobox(
            form,
            textvariable=self.correct_var,
            values=("correct", "incorrect"),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky="w", padx=6)
        tk.Label(form, text="최대 경계 오차(ms)").grid(row=0, column=2, sticky="w")
        self.boundary_var = tk.StringVar(value="")
        tk.Entry(form, textvariable=self.boundary_var, width=10).grid(
            row=0, column=3, sticky="w", padx=6
        )
        tk.Label(form, text="틀린 자동 대응 개수").grid(row=0, column=4, sticky="w")
        self.incorrect_count_var = tk.StringVar(value="")
        tk.Entry(form, textvariable=self.incorrect_count_var, width=6).grid(
            row=0, column=5, sticky="w", padx=6
        )
        tk.Label(form, text="annotation fidelity").grid(
            row=1, column=0, sticky="w"
        )
        self.annotation_var = tk.StringVar(value="")
        ttk.Combobox(
            form,
            textvariable=self.annotation_var,
            values=ANNOTATION_FIDELITY_VALUES,
            state="readonly",
            width=30,
        ).grid(row=1, column=1, columnspan=3, sticky="w", padx=6)
        tk.Label(form, text="메모").grid(row=2, column=0, sticky="nw", pady=4)
        self.notes = tk.Text(form, height=3, width=80)
        self.notes.grid(row=2, column=1, columnspan=5, sticky="we", pady=4)
        tk.Button(form, text="원자 저장", command=self.on_save).grid(
            row=3, column=0, pady=4, sticky="w"
        )

        self.refresh()

    def report_error(self, error: Exception) -> None:
        if isinstance(error, urllib.error.HTTPError):
            detail = error.read().decode("utf-8", "replace")
            message = f"HTTP {error.code}: {detail}"
            if error.code == 403:
                message += " — 토큰이 틀렸거나 서버가 재시작됐습니다."
        elif isinstance(error, urllib.error.URLError):
            message = f"서버 연결 실패: {error.reason} — SSH 터널을 확인하세요."
        else:
            message = str(error)
        self.error_var.set(message)
        messagebox.showerror("오류", message)

    def refresh(self, keep_selection: bool = False) -> None:
        selected = self.listbox.curselection()
        try:
            status = self.client.status()
            self.items = self.client.items()
        except Exception as error:  # noqa: BLE001 - surfaced to the reviewer
            self.report_error(error)
            return
        self.error_var.set("")
        self.status_var.set(
            f"완료 {status['completed_count']} / {status['expected_count']}"
            f" · reviewer hash: {status['reviewer_id_sha256'][:16]}…"
        )
        self.listbox.delete(0, "end")
        for item in self.items:
            mark = "✓" if item["reviewed"] else "·"
            self.listbox.insert(
                "end",
                f"{mark} {item['audit_index']:>3} {item['item_sha256'][:12]}"
                f" {item['duration_seconds']:.1f}s",
            )
        if keep_selection and selected:
            self.listbox.selection_set(selected[0])
            self.listbox.see(selected[0])

    def on_select(self, _event: object) -> None:
        selected = self.listbox.curselection()
        if not selected:
            return
        summary = self.items[selected[0]]
        try:
            item = self.client.item(summary["item_sha256"])
            self.player.load(self.client.audio_bytes(summary["item_sha256"]))
        except Exception as error:  # noqa: BLE001 - surfaced to the reviewer
            self.report_error(error)
            return
        self.error_var.set("")
        self.current = item
        self.title_var.set(f"#{item['audit_index']} · {item['item_sha256'][:24]}…")
        detail = {
            "automatic_phone_alignments": item["automatic_phone_alignments"],
            "target_phone_intervals": item["target_phone_intervals"],
            "realized_phone_intervals": item["realized_phone_intervals"],
            "metadata": {
                key: item[key]
                for key in (
                    "split",
                    "l1",
                    "gender",
                    "proficiency",
                    "duration_seconds",
                )
            },
        }
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", json.dumps(detail, ensure_ascii=False, indent=2))
        self.detail.configure(state="disabled")
        review = item.get("review") or {}
        correct = review.get("alignment_correct")
        self.correct_var.set(
            "correct"
            if correct is True
            else "incorrect"
            if correct is False
            else DEFAULT_ALIGNMENT_CORRECT
        )
        boundary = review.get("maximum_boundary_error_ms")
        self.boundary_var.set(
            DEFAULT_MAXIMUM_BOUNDARY_ERROR_MS if boundary is None else str(boundary)
        )
        self.annotation_var.set(review.get("annotation_fidelity") or "")
        incorrect_count = review.get("incorrect_interval_count")
        self.incorrect_count_var.set(
            DEFAULT_INCORRECT_INTERVAL_COUNT
            if incorrect_count is None
            else str(incorrect_count)
        )
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", review.get("notes") or "")

    def on_play(self) -> None:
        try:
            self.player.play()
        except Exception as error:  # noqa: BLE001 - surfaced to the reviewer
            self.report_error(error)

    def on_open_externally(self) -> None:
        try:
            self.player.open_externally()
        except Exception as error:  # noqa: BLE001 - surfaced to the reviewer
            self.report_error(error)

    def on_save(self) -> None:
        if self.current is None:
            return
        if self.correct_var.get() not in {"correct", "incorrect"}:
            messagebox.showwarning("입력 필요", "정렬 정확성을 선택하세요.")
            return
        annotation_fidelity = self.annotation_var.get()
        if annotation_fidelity not in ANNOTATION_FIDELITY_VALUES:
            messagebox.showwarning(
                "입력 필요", "annotation fidelity를 선택하세요."
            )
            return
        try:
            boundary = float(self.boundary_var.get())
        except ValueError:
            messagebox.showwarning(
                "입력 필요", "최대 경계 오차(ms)를 숫자로 입력하세요."
            )
            return
        try:
            incorrect_count = int(self.incorrect_count_var.get())
        except ValueError:
            messagebox.showwarning(
                "입력 필요", "틀린 자동 대응 개수를 정수로 입력하세요."
            )
            return
        is_correct = self.correct_var.get() == "correct"
        total_intervals = len(self.current.get("automatic_phone_alignments", []))
        if is_correct and incorrect_count != 0:
            messagebox.showwarning(
                "입력 확인",
                "정렬 정확성이 correct이면 틀린 자동 대응 개수는 0이어야 합니다.",
            )
            return
        if not is_correct and not (1 <= incorrect_count <= total_intervals):
            messagebox.showwarning(
                "입력 확인",
                f"정렬 정확성이 incorrect이면 틀린 자동 대응 개수는 1~"
                f"{total_intervals} 사이여야 합니다.",
            )
            return
        body = {
            "alignment_correct": is_correct,
            "maximum_boundary_error_ms": boundary,
            "incorrect_interval_count": incorrect_count,
            "annotation_fidelity": annotation_fidelity,
            "notes": self.notes.get("1.0", "end-1c"),
        }
        try:
            self.client.save(
                self.current["item_sha256"],
                body,
                replace=bool(self.current["reviewed"]),
            )
        except Exception as error:  # noqa: BLE001 - surfaced to the reviewer
            self.report_error(error)
            return
        self.current["reviewed"] = True
        self.refresh(keep_selection=True)


def main() -> int:
    if tk is None:
        raise SystemExit(
            "tkinter is unavailable in this Python; run this client on a "
            "desktop machine (standard Windows Python includes tkinter)"
        )
    args = parse_args()
    base_url, token = split_url_and_token(args.url, args.token)
    root = tk.Tk()
    if not token:
        token = simpledialog.askstring(
            "Review token",
            "서버 터미널의 #token= 값 또는 전체 URL을 붙여넣으세요:",
            parent=root,
        )
        if token and "#token=" in token:
            token = token.split("#token=", 1)[1]
    if not token:
        messagebox.showerror("오류", "review 토큰이 필요합니다.")
        return 1
    ReviewApp(root, ReviewClient(base_url, token.strip()))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
