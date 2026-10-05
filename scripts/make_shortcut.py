"""Build and sign the "Resume Podcast" Shortcut.

The Shortcut reads podcast-sync/resume.txt from the Shortcuts iCloud folder
(the helper writes it) and opens the link inside, which starts Apple Podcasts
on the right episode at the right time.

    python3 scripts/make_shortcut.py            # writes shortcut/Resume Podcast.shortcut
    open "shortcut/Resume Podcast.shortcut"     # add it; iCloud syncs it to the iPhone
"""
import plistlib
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "shortcut"
NAME = "Resume Podcast"


def output_of(action_uuid: str, name: str):
    return {
        "Value": {"OutputUUID": action_uuid, "Type": "ActionOutput", "OutputName": name},
        "WFSerializationType": "WFTextTokenAttachment",
    }


def build() -> dict:
    get_file, get_text = (str(uuid.uuid4()).upper() for _ in range(2))
    actions = [
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.documentpicker.open",
            "WFWorkflowActionParameters": {
                "UUID": get_file,
                "WFGetFilePath": "podcast-sync/resume.txt",
                "WFShowFilePicker": False,
                "WFFileErrorIfNotFound": True,
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.detect.text",
            "WFWorkflowActionParameters": {"UUID": get_text, "WFInput": output_of(get_file, "File")},
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
            "WFWorkflowActionParameters": {"WFInput": output_of(get_text, "Text")},
        },
    ]
    return {
        "WFWorkflowActions": actions,
        "WFWorkflowClientVersion": "2607.0.2",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 2071128575, "WFWorkflowIconGlyphNumber": 59446},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [],
        "WFQuickActionSurfaces": [],
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowHasOutputFallback": False,
    }


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    unsigned = OUT_DIR / f"{NAME}.unsigned.shortcut"
    signed = OUT_DIR / f"{NAME}.shortcut"
    unsigned.write_bytes(plistlib.dumps(build(), fmt=plistlib.FMT_BINARY))
    r = subprocess.run(
        ["shortcuts", "sign", "--mode", "people-who-know-me", "--input", str(unsigned), "--output", str(signed)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not signed.exists():
        print("signing failed:", r.stderr or r.stdout, file=sys.stderr)
        return 1
    unsigned.unlink()
    print(signed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
