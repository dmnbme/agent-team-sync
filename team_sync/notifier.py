"""Desktop notifications that open the person's agent app when clicked.

macOS: a tiny AppleScript applet, built once per user with osacompile (no installs), posts the notifications, so they
show as "team-sync" rather than Script Editor. Clicking one launches the applet again; with nothing pending it opens the
app that last ran a team-sync hook on this computer (Claude, Codex, a terminal, an editor), read from the
__CFBundleIdentifier that macOS gives every process an app starts.
Windows: a native toast as "team-sync" with the team icon (a tray balloon where toasts fail); clicking it starts the executable of the app that last ran a hook (found once, by walking up the hook's parent processes). Linux: notify-send, no click action.
"""
import hashlib
import json
import os
import pathlib
import platform
import subprocess
import sys

HOME = pathlib.Path.home() / '.team-sync' / 'notify'
APP = HOME / 'team-sync.app'
AGENT = HOME / 'agent'                   # macOS: bundle id; Windows: path of the .exe to start
KIND = HOME / 'agent_kind'
PENDING = HOME / 'pending'
BUNDLE_ID = 'com.agent-team-sync.notify'   # + a hash of the icon: Notification Center caches icons per bundle id
TOAST_ID = 'AgentTeamSync.Notify'          # Windows: the AppUserModelId toasts are posted under
TOAST_CLSID = '{6B1E2A54-0F3C-4D8E-9A71-3C5D7E2B9F10}'   # Windows: the toast's activator; a click runs OPENER
OPENER = HOME / 'toast_activator.pyw'      # from resources/; COM runs it for a click

# Run by the applet. With a pending file: post it (first line title, the rest body) and quit. Without: it was launched
# by a click on one of its notifications, so open the agent app.
APPLET = '''on run
	set base to (POSIX path of (path to home folder)) & ".team-sync/notify/"
	set txt to ""
	try
		set txt to read (POSIX file (base & "pending")) as «class utf8»
	end try
	if txt is not "" then
		do shell script "rm -f " & quoted form of (base & "pending")
		set ps to paragraphs of txt
		set t to item 1 of ps
		set b to ""
		if (count of ps) > 1 then
			set AppleScript's text item delimiters to " "
			set b to (items 2 thru -1 of ps) as text
		end if
		display notification b with title t
		delay 1
	else
		set target to ""
		try
			set target to paragraph 1 of (read (POSIX file (base & "agent")) as «class utf8»)
		end try
		if target is not "" then do shell script "open -b " & quoted form of target
	end if
end run
'''
SYNC_ICON = '/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/Sync.icns'   # default: the system sync arrows


def _stamp(icon):
    h = hashlib.sha1((APPLET + BUNDLE_ID).encode())
    try:
        h.update(pathlib.Path(icon).read_bytes())
    except OSError:
        pass
    return h.hexdigest()[:12]


def _icns(icon, out):
    """The team's icon (notify.icon in the config: .icns, or a square .png) as an .icns file at `out`."""
    if icon.lower().endswith('.icns'):
        return subprocess.run(['cp', icon, out], capture_output=True).returncode == 0
    iconset = HOME / 'icon.iconset'
    subprocess.run(['rm', '-rf', str(iconset)], capture_output=True); iconset.mkdir()
    for px in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            subprocess.run(['sips', '-z', str(px * scale), str(px * scale), icon, '--out',
                            str(iconset / f'icon_{px}x{px}{"@2x" if scale == 2 else ""}.png')], capture_output=True)
    ok = subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', out], capture_output=True).returncode == 0
    subprocess.run(['rm', '-rf', str(iconset)], capture_output=True)
    return ok
SKIP = {'python', 'python3', 'pythonw', 'py', 'cmd', 'powershell', 'pwsh', 'conhost', 'bash', 'sh', 'git', 'node',
        'uv', 'wsl', 'openconsole', 'windowsterminal-helper'}


def remember_agent(kind):
    """Called from every hook: note which app this person runs their agent in. Cheap; writes only when it changes."""
    if os.environ.get('TEAM_SYNC_NO_NOTIFY'):                   # selftest: its hooks must not repoint the real person's clicks
        return
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        sysname = platform.system()
        if sysname == 'Darwin':
            bid = os.environ.get('__CFBundleIdentifier', '').strip()
            if bid and not bid.startswith(BUNDLE_ID) and (not AGENT.exists() or AGENT.read_text(encoding='utf-8').strip() != bid):
                AGENT.write_text(bid + '\n', encoding='utf-8')
        elif sysname == 'Windows':
            if KIND.exists() and KIND.read_text(encoding='utf-8').strip() == kind and AGENT.exists():
                return
            exe = _windows_host_exe()
            if exe:
                AGENT.write_text(exe + '\n', encoding='utf-8')
            KIND.write_text(kind + '\n', encoding='utf-8')
    except Exception:  # noqa: BLE001
        pass


def _windows_host_exe():
    ps = ("$p = Get-CimInstance Win32_Process -Filter \"ProcessId=$PID\"; $out = @(); "
          "for ($i = 0; $i -lt 12 -and $p; $i++) { $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($p.ParentProcessId)\"; "
          "if ($p) { $out += $p.ExecutablePath } }; $out | ConvertTo-Json -Compress")
    r = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True, timeout=20,
                       creationflags=0x08000000)
    try:
        chain = json.loads(r.stdout or '[]')
    except ValueError:
        return ''
    for exe in [chain] if isinstance(chain, str) else chain:
        if exe and pathlib.Path(exe).stem.lower() not in SKIP:
            return exe
    return ''


def _applet(icon=None):
    """Build (or rebuild after an update or an icon change) the macOS applet. None means: fall back to plain osascript."""
    icon = icon if icon and os.path.exists(icon) else SYNC_ICON
    stamp, want = HOME / 'app_stamp', _stamp(icon)
    if APP.exists() and stamp.exists() and stamp.read_text(encoding='utf-8').strip() == want:
        return APP
    HOME.mkdir(parents=True, exist_ok=True)
    src = HOME / 'notifier.applescript'
    src.write_text(APPLET, encoding='utf-8')
    subprocess.run(['rm', '-rf', str(APP)], capture_output=True)
    if subprocess.run(['osacompile', '-o', str(APP), str(src)], capture_output=True).returncode != 0:
        return None
    plist = str(APP / 'Contents' / 'Info.plist')
    for k, v in (('CFBundleIdentifier', f'{BUNDLE_ID}.{want[:8]}'), ('CFBundleName', 'team-sync'), ('CFBundleDisplayName', 'team-sync')):
        subprocess.run(['plutil', '-replace', k, '-string', v, plist], capture_output=True)
    subprocess.run(['plutil', '-replace', 'LSUIElement', '-bool', 'YES', plist], capture_output=True)   # no Dock icon
    if os.path.exists(icon):                                    # instead of the Script Editor scroll
        _icns(icon, str(APP / 'Contents' / 'Resources' / 'applet.icns'))
    subprocess.run(['codesign', '--force', '--deep', '-s', '-', str(APP)], capture_output=True)
    subprocess.run(['/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister',
                    '-f', str(APP)], capture_output=True)       # so Notification Center picks up the new icon
    stamp.write_text(want, encoding='utf-8')
    return APP


def show(title, text, level='Warning', icon=None):
    clean = lambda x: ' '.join(x.replace('"', "'").replace("'", '').replace('\\', '/').split())
    title, text = clean(title)[:60], clean(text)[:200]
    sysname = platform.system()
    if sysname == 'Darwin':
        app = None
        try:
            app = _applet(icon)
        except Exception:  # noqa: BLE001
            pass
        if app:
            PENDING.write_text(f'{title}\n{text}\n', encoding='utf-8')
            if subprocess.run(['open', '-g', '-n', str(app)], capture_output=True).returncode == 0:
                return
        subprocess.run(['osascript', '-e', f'display notification "{text}" with title "{title}"'], capture_output=True)
    elif sysname == 'Windows':
        target = AGENT.read_text(encoding='utf-8').strip().lstrip('\ufeff') if AGENT.exists() else ''
        png = icon if icon and icon.lower().endswith('.png') and os.path.exists(icon) else ''
        esc = lambda x: x.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        logo = f'<image placement="appLogoOverride" src="{pathlib.Path(png).as_uri()}"/>' if png else ''
        xml = f'<toast launch="open"><visual><binding template="ToastGeneric"><text>{esc(title)}</text><text>{esc(text)}</text>{logo}</binding></visual></toast>'
        # A native toast under its own name and icon: a per-user AppUserModelId in the registry, no shortcut or admin.
        # Its CustomActivator is a per-user COM server, OPENER: a click, on the popup or later in the Action Center, runs
        # it; it answers the activation (so Windows removes that one toast) and starts the agent app. (Windows PowerShell
        # cannot subscribe to the toast's Activated event, and toasts refuse protocol launches to non-system handlers.)
        # Where toasts fail: a tray balloon.
        HOME.mkdir(parents=True, exist_ok=True)
        src = (pathlib.Path(__file__).parent / 'resources' / 'toast_activator.pyw').read_text(encoding='utf-8')
        if not OPENER.exists() or OPENER.read_text(encoding='utf-8') != src:
            OPENER.write_text(src, encoding='utf-8')
        pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
        server = f'"{pyw if os.path.exists(pyw) else sys.executable}" "{OPENER}"'      # COM appends -Embedding; ignored
        toast = ("$ErrorActionPreference = 'Stop'; "
                 f"$s = 'HKCU:/Software/Classes/CLSID/{TOAST_CLSID}/LocalServer32'; New-Item -Force $s | Out-Null; "
                 f"Set-ItemProperty $s '(default)' '{server}'; "
                 f"$k = 'HKCU:/Software/Classes/AppUserModelId/{TOAST_ID}'; New-Item -Force $k | Out-Null; "
                 f"Set-ItemProperty $k DisplayName 'team-sync'; Set-ItemProperty $k CustomActivator '{TOAST_CLSID}'; " +
                 (f"Set-ItemProperty $k IconUri '{png}'; " if png else '') +
                 "[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]; "
                 "[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]; "
                 f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('{xml}'); "
                 f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{TOAST_ID}').Show((New-Object Windows.UI.Notifications.ToastNotification $x)); ")
        balloon = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
                   f"$b = New-Object System.Windows.Forms.NotifyIcon; $b.Icon = [System.Drawing.SystemIcons]::{level}; $b.Visible = $true; "
                   "$script:hit = $false; $b.add_BalloonTipClicked({ $script:hit = $true }); "
                   f"$b.ShowBalloonTip(10000, '{title}', '{text}', [System.Windows.Forms.ToolTipIcon]::{level}); "
                   "$t = Get-Date; while (((Get-Date) - $t).TotalSeconds -lt 12 -and -not $script:hit) { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 100 }; "
                   "$b.Dispose(); " + (f"if ($script:hit) {{ Start-Process '{target}' }}" if target else ''))
        ps = f"try {{ {toast} }} catch {{ {balloon} }}"
        subprocess.Popen(['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps], creationflags=0x08000000)
    elif sysname == 'Linux':
        subprocess.run(['notify-send', title, text], capture_output=True)
