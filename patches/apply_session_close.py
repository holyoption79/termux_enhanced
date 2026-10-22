"""Add a per-session close button to the Termux sessions drawer.

Run from the root of a termux-app checkout:  python3 apply_session_close.py
Every replacement asserts its anchor exists exactly once, so an upstream
change makes this fail loudly instead of patching the wrong place.
"""
import pathlib
import sys

APP = pathlib.Path("app/src/main")
JAVA = APP / "java/com/termux/app/terminal"


def replace_once(path, old, new):
    text = path.read_text(encoding="utf-8")
    if new in text:
        return  # already applied
    if text.count(old) != 1:
        sys.exit(f"{path}: anchor found {text.count(old)} times, expected 1:\n{old}")
    path.write_text(text.replace(old, new), encoding="utf-8")


# Row layout: title on the left, a close button on the right. The row itself
# carries the selection background so the button sits on the highlight.
(APP / "res/layout/item_terminal_sessions_list.xml").write_text(
    """<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:id="@+id/session_row"
    android:layout_width="fill_parent"
    android:layout_height="?android:attr/listPreferredItemHeight"
    android:background="@drawable/session_background_selected"
    android:orientation="horizontal">

    <com.google.android.material.textview.MaterialTextView
        android:id="@+id/session_title"
        android:layout_width="0dp"
        android:layout_height="match_parent"
        android:layout_weight="1"
        android:ellipsize="marquee"
        android:gravity="start|center_vertical"
        android:padding="6dip"
        android:textSize="14sp" />

    <!-- Not focusable, so tapping the rest of the row still selects the session. -->
    <TextView
        android:id="@+id/session_close"
        android:layout_width="48dp"
        android:layout_height="match_parent"
        android:background="?android:attr/selectableItemBackgroundBorderless"
        android:contentDescription="@string/action_close_session"
        android:focusable="false"
        android:focusableInTouchMode="false"
        android:gravity="center"
        android:text="\\u2715"
        android:textSize="20sp" />

</LinearLayout>
""",
    encoding="utf-8",
)

replace_once(
    APP / "res/values/strings.xml",
    """    <string name="title_confirm_kill_process">Really kill this session?</string>""",
    """    <string name="title_confirm_kill_process">Really kill this session?</string>
    <string name="action_close_session">Close</string>
    <string name="title_close_session">Close session [%1$d]?</string>
    <string name="msg_close_running_session">Its running programs are killed, even if the session is stuck.</string>
    <string name="msg_close_finished_session">This session has already exited.</string>""",
)

# --- drawer adapter -------------------------------------------------------
lv = JAVA / "TermuxSessionsListViewController.java"
replace_once(lv, "import android.annotation.SuppressLint;\n",
             "import android.annotation.SuppressLint;\nimport android.app.AlertDialog;\n")
replace_once(
    lv,
    """        TextView sessionTitleView = sessionRowView.findViewById(R.id.session_title);

        TerminalSession sessionAtRow = getItem(position).getTerminalSession();""",
    """        TextView sessionTitleView = sessionRowView.findViewById(R.id.session_title);
        TextView sessionCloseView = sessionRowView.findViewById(R.id.session_close);

        TermuxSession termuxSessionAtRow = getItem(position);
        sessionCloseView.setOnClickListener(v -> confirmCloseSession(termuxSessionAtRow));

        TerminalSession sessionAtRow = termuxSessionAtRow.getTerminalSession();""",
)
replace_once(
    lv,
    """        if (shouldEnableDarkTheme) {
            sessionTitleView.setBackground(""",
    """        if (shouldEnableDarkTheme) {
            sessionRowView.setBackground(""",
)
replace_once(
    lv,
    """        sessionTitleView.setTextColor(color);
        return sessionRowView;
    }
""",
    """        sessionTitleView.setTextColor(color);
        sessionCloseView.setTextColor(defaultColor);
        return sessionRowView;
    }

    /** Ask before closing, since closing kills whatever the session is running. */
    private void confirmCloseSession(@NonNull TermuxSession termuxSession) {
        final TerminalSession session = termuxSession.getTerminalSession();
        if (session == null) return;
        int number = getPosition(termuxSession) + 1;

        new AlertDialog.Builder(mActivity)
            .setTitle(mActivity.getString(R.string.title_close_session, number))
            .setMessage(session.isRunning() ? R.string.msg_close_running_session : R.string.msg_close_finished_session)
            .setPositiveButton(R.string.action_close_session, (dialog, id) -> {
                dialog.dismiss();
                mActivity.getTermuxTerminalSessionClient().closeSession(session);
            })
            .setNegativeButton(android.R.string.cancel, null)
            .show();
    }
""",
)

# --- session client: kill, then remove once the shell has exited ----------
sc = JAVA / "TermuxTerminalSessionActivityClient.java"
replace_once(sc, "import android.media.SoundPool;\n",
             "import android.media.SoundPool;\nimport android.system.Os;\nimport android.system.OsConstants;\n")
replace_once(sc, "import java.io.File;\n",
             "import java.io.BufferedReader;\nimport java.io.File;\n")
replace_once(sc, "import java.io.FileInputStream;\n",
             "import java.io.FileInputStream;\nimport java.io.FileReader;\n")
replace_once(sc, "import java.util.Properties;\n",
             "import java.util.HashSet;\nimport java.util.Properties;\nimport java.util.Set;\n")
replace_once(
    sc,
    """    private static final String LOG_TAG = "TermuxTerminalSessionActivityClient";
""",
    """    private static final String LOG_TAG = "TermuxTerminalSessionActivityClient";

    /** Sessions closed from the drawer; each is removed as soon as its shell exits. */
    private final Set<TerminalSession> mSessionsPendingClose = new HashSet<>();
""",
)
replace_once(
    sc,
    """        int index = service.getIndexOfSession(finishedSession);

        // For plugin commands that expect the result back""",
    """        if (mSessionsPendingClose.remove(finishedSession)) {
            if (service.getIndexOfSession(finishedSession) >= 0)
                removeFinishedSession(finishedSession);
            return;
        }

        int index = service.getIndexOfSession(finishedSession);

        // For plugin commands that expect the result back""",
)
replace_once(
    sc,
    """    public void renameSession(final TerminalSession sessionToRename) {""",
    """    /**
     * Close a session from the drawer even when it is stuck: SIGKILL its foreground
     * process group and its shell, then drop it in {@link #onSessionFinished}.
     */
    public void closeSession(@NonNull TerminalSession session) {
        if (!session.isRunning()) {
            if (mActivity.getTermuxService() != null && mActivity.getTermuxService().getIndexOfSession(session) >= 0)
                removeFinishedSession(session);
            return;
        }

        mSessionsPendingClose.add(session);
        killForegroundProcessGroup(session.getPid());
        session.finishIfRunning();
    }

    /** SIGKILL the terminal's foreground job (tpgid, field 8 of /proc/PID/stat) if it is not the shell. */
    private static void killForegroundProcessGroup(int shellPid) {
        if (shellPid <= 0) return;
        try (BufferedReader reader = new BufferedReader(new FileReader("/proc/" + shellPid + "/stat"))) {
            String stat = reader.readLine();
            String[] fields = stat.substring(stat.lastIndexOf(')') + 2).split(" ");
            int foregroundGroup = Integer.parseInt(fields[5]);
            if (foregroundGroup > 0 && foregroundGroup != shellPid)
                Os.kill(-foregroundGroup, OsConstants.SIGKILL);
        } catch (Exception e) {
            // The shell's SIGKILL still hangs up the terminal, which ends most jobs.
            Logger.logWarn(LOG_TAG, "Failed to kill foreground process group of pid " + shellPid + ": " + e.getMessage());
        }
    }

    public void renameSession(final TerminalSession sessionToRename) {""",
)

print("session close patch applied")
