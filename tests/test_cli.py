from ghostbooking.cli import main


def test_check_and_dry_run_write_nothing(example):
    sheet = example.parent / "room-bookings.xlsx"
    before = sheet.read_bytes()
    assert main(["check", "-c", str(example), "--now", "2026-11-23 07:30"]) == 0
    assert main(["tick", "-c", str(example), "--now", "2026-11-23 07:30", "--dry-run"]) == 0
    assert sheet.read_bytes() == before


def test_tick_skips_with_exit_code_2_when_open(example):
    (example.parent / "~$room-bookings.xlsx").touch()
    assert main(["tick", "-c", str(example), "--now", "2026-11-23 07:30"]) == 2


def test_missing_file_is_a_clear_error(tmp_path):
    (tmp_path / "config.yaml").write_text("source:\n  path: nope.xlsx\n", encoding="utf-8")
    assert main(["check", "-c", str(tmp_path / "config.yaml")]) == 1
