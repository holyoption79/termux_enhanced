"""Draw the extra keys as large as their keys allow.

Every label is auto-sized to fill its key on one line, up to a per-kind maximum:
- the drawer and keyboard-toggle keys are the way out when the soft keyboard is
  stuck, so they get the widest columns and the largest symbols;
- keys labelled with a word (Esc, Ctrl, Alt) get wider columns so the word can
  be drawn large without wrapping;
- single-symbol keys (arrows, tab) fill the rest.

Run from the root of a termux-app checkout:  python3 apply_extra_keys.py
Every replacement asserts its anchor exists exactly once, so an upstream
change makes this fail loudly instead of patching the wrong place.
"""
import pathlib
import sys

VIEW = pathlib.Path(
    "termux-shared/src/main/java/com/termux/shared/termux/extrakeys/ExtraKeysView.java")


def replace_once(path, old, new):
    text = path.read_text(encoding="utf-8")
    if new in text:
        return  # already applied
    if text.count(old) != 1:
        sys.exit(f"{path}: anchor found {text.count(old)} times, expected 1:\n{old}")
    path.write_text(text.replace(old, new), encoding="utf-8")


replace_once(
    VIEW,
    "import android.util.AttributeSet;\n",
    "import android.util.AttributeSet;\nimport android.util.TypedValue;\n",
)

replace_once(
    VIEW,
    "import androidx.annotation.Nullable;\n",
    "import androidx.annotation.Nullable;\nimport androidx.core.widget.TextViewCompat;\n",
)

replace_once(
    VIEW,
    """                button.setText(buttonInfo.getDisplay());
                button.setTextColor(mButtonTextColor);
                button.setAllCaps(mButtonTextAllCaps);
                button.setPadding(0, 0, 0, 0);
""",
    """                button.setText(buttonInfo.getDisplay());
                button.setTextColor(mButtonTextColor);
                button.setAllCaps(mButtonTextAllCaps);
                button.setPadding(0, 0, 0, 0);
                setKeyTextAutoSize(button, buttonInfo);
""",
)

replace_once(
    VIEW,
    "                param.columnSpec = GridLayout.spec(col, GridLayout.FILL, 1.f);\n",
    "                param.columnSpec = GridLayout.spec(col, GridLayout.FILL, getKeyColumnWeight(buttonInfo));\n",
)

replace_once(
    VIEW,
    """    /** Check whether a {@link ExtraKeyButton} is a {@link SpecialButton}. */
    public boolean isSpecialButton(ExtraKeyButton button) {""",
    """    /** Column width weight of the {@link #isLargeKey(ExtraKeyButton)} keys; symbol keys use 1. */
    public static final float LARGE_KEY_COLUMN_WEIGHT = 1.5f;
    /** Column width weight of the {@link #isWordKey(ExtraKeyButton)} keys. */
    public static final float WORD_KEY_COLUMN_WEIGHT = 1.35f;
    /** Largest label sizes in sp. Labels shrink below these to fit their key on one line. */
    public static final int LARGE_KEY_MAX_TEXT_SP = 34;
    public static final int SYMBOL_KEY_MAX_TEXT_SP = 23;
    public static final int WORD_KEY_MAX_TEXT_SP = 17;
    /** Smallest label size in sp. */
    public static final int KEY_MIN_TEXT_SP = 8;

    /**
     * Check whether a {@link ExtraKeyButton} is the sessions drawer or keyboard toggle key, which
     * stay usable when the soft keyboard is stuck and so are drawn larger than the other keys.
     */
    public boolean isLargeKey(ExtraKeyButton button) {
        return !button.isMacro() && ("DRAWER".equals(button.getKey()) || "KEYBOARD".equals(button.getKey()));
    }

    /** Check whether a {@link ExtraKeyButton} is labelled with a word (Esc, Ctrl, ...) rather than a symbol. */
    public boolean isWordKey(ExtraKeyButton button) {
        String display = button.getDisplay();
        return display != null && display.codePointCount(0, display.length()) > 1;
    }

    /** Get the column width weight of a {@link ExtraKeyButton}. */
    public float getKeyColumnWeight(ExtraKeyButton button) {
        if (isLargeKey(button)) return LARGE_KEY_COLUMN_WEIGHT;
        return isWordKey(button) ? WORD_KEY_COLUMN_WEIGHT : 1.f;
    }

    /** Draw a key's label as large as its key allows, on one line, up to a per-kind maximum. */
    public void setKeyTextAutoSize(MaterialButton button, ExtraKeyButton buttonInfo) {
        int maxSp = isLargeKey(buttonInfo) ? LARGE_KEY_MAX_TEXT_SP
            : isWordKey(buttonInfo) ? WORD_KEY_MAX_TEXT_SP : SYMBOL_KEY_MAX_TEXT_SP;
        button.setMaxLines(1);
        TextViewCompat.setAutoSizeTextTypeUniformWithConfiguration(button,
            KEY_MIN_TEXT_SP, maxSp, 1, TypedValue.COMPLEX_UNIT_SP);
    }

    /** Check whether a {@link ExtraKeyButton} is a {@link SpecialButton}. */
    public boolean isSpecialButton(ExtraKeyButton button) {""",
)

print("extra keys patch applied")
