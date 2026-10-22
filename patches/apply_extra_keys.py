"""Make the drawer and keyboard-toggle extra keys bigger than the rest.

These two keys are the way out when the soft keyboard is stuck, so they get a
wider column and a larger symbol. Everything else in the row stays as is.

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
    """                button.setText(buttonInfo.getDisplay());
                button.setTextColor(mButtonTextColor);
                button.setAllCaps(mButtonTextAllCaps);
                button.setPadding(0, 0, 0, 0);
""",
    """                button.setText(buttonInfo.getDisplay());
                button.setTextColor(mButtonTextColor);
                button.setAllCaps(mButtonTextAllCaps);
                button.setPadding(0, 0, 0, 0);

                final boolean isLargeKey = isLargeKey(buttonInfo);
                if (isLargeKey)
                    button.setTextSize(TypedValue.COMPLEX_UNIT_PX, button.getTextSize() * LARGE_KEY_TEXT_SCALE);
""",
)

replace_once(
    VIEW,
    "                param.columnSpec = GridLayout.spec(col, GridLayout.FILL, 1.f);\n",
    "                param.columnSpec = GridLayout.spec(col, GridLayout.FILL, isLargeKey ? LARGE_KEY_COLUMN_WEIGHT : 1.f);\n",
)

replace_once(
    VIEW,
    """    /** Check whether a {@link ExtraKeyButton} is a {@link SpecialButton}. */
    public boolean isSpecialButton(ExtraKeyButton button) {""",
    """    /** Text size multiplier for the {@link #isLargeKey(ExtraKeyButton)} keys. */
    public static final float LARGE_KEY_TEXT_SCALE = 1.8f;
    /** Column width weight for the {@link #isLargeKey(ExtraKeyButton)} keys; other keys use 1. */
    public static final float LARGE_KEY_COLUMN_WEIGHT = 1.5f;

    /**
     * Check whether a {@link ExtraKeyButton} is the sessions drawer or keyboard toggle key, which
     * stay usable when the soft keyboard is stuck and so are drawn larger than the other keys.
     */
    public boolean isLargeKey(ExtraKeyButton button) {
        return !button.isMacro() && ("DRAWER".equals(button.getKey()) || "KEYBOARD".equals(button.getKey()));
    }

    /** Check whether a {@link ExtraKeyButton} is a {@link SpecialButton}. */
    public boolean isSpecialButton(ExtraKeyButton button) {""",
)

print("extra keys patch applied")
