// Win32 interop for the SYSTEM watchdog, loaded with Add-Type.
// A SYSTEM process (session 0) can't show UI to the user, so the watchdog launches the UI agent into the
// active console session with the logged-on user's own token (WTSQueryUserToken needs SeTcbPrivilege,
// which SYSTEM has). The UI therefore runs with the child's normal, unprivileged rights.
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;

public static class KcNative
{
    public const uint NoSession = 0xFFFFFFFF;

    [DllImport("kernel32.dll")]
    private static extern uint WTSGetActiveConsoleSessionId();

    [DllImport("wtsapi32.dll", SetLastError = true)]
    private static extern bool WTSQueryUserToken(uint sessionId, out IntPtr token);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool DuplicateTokenEx(IntPtr existingToken, uint desiredAccess, IntPtr tokenAttributes,
        int impersonationLevel, int tokenType, out IntPtr newToken);

    [DllImport("userenv.dll", SetLastError = true)]
    private static extern bool CreateEnvironmentBlock(out IntPtr environment, IntPtr token, bool inherit);

    [DllImport("userenv.dll", SetLastError = true)]
    private static extern bool DestroyEnvironmentBlock(IntPtr environment);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION
    {
        public IntPtr hProcess, hThread;
        public int dwProcessId, dwThreadId;
    }

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CreateProcessAsUser(IntPtr token, string applicationName, StringBuilder commandLine,
        IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint creationFlags,
        IntPtr environment, string currentDirectory, ref STARTUPINFO startupInfo, out PROCESS_INFORMATION processInfo);

    private const uint MAXIMUM_ALLOWED = 0x02000000;
    private const int SecurityImpersonation = 2;
    private const int TokenPrimary = 1;
    private const uint CREATE_UNICODE_ENVIRONMENT = 0x00000400;
    private const uint CREATE_NO_WINDOW = 0x08000000;

    public static uint ActiveConsoleSessionId()
    {
        return WTSGetActiveConsoleSessionId();
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inherit, int processId);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool QueryFullProcessImageNameW(IntPtr process, int flags, StringBuilder name, ref int size);

    private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

    /// <summary>Full path of a process's program file, or null if it can't be read. Works for most
    /// processes where Process.MainModule doesn't (other bitness, protected processes).</summary>
    public static string ProcessPath(int processId)
    {
        IntPtr h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, processId);
        if (h == IntPtr.Zero) return null;
        try
        {
            StringBuilder sb = new StringBuilder(1024);
            int size = sb.Capacity;
            return QueryFullProcessImageNameW(h, 0, sb, ref size) ? sb.ToString(0, size) : null;
        }
        finally
        {
            CloseHandle(h);
        }
    }

    /// <summary>SID of the user logged on to the session, or null if nobody is (e.g. the logon screen).</summary>
    public static string GetSessionUserSid(uint sessionId)
    {
        IntPtr token;
        if (sessionId == NoSession || !WTSQueryUserToken(sessionId, out token)) return null;
        try
        {
            using (WindowsIdentity identity = new WindowsIdentity(token))
            {
                return identity.User == null ? null : identity.User.Value;
            }
        }
        finally
        {
            CloseHandle(token);
        }
    }

    /// <summary>Start a process on the interactive desktop of the session, as its logged-on user. Returns the PID.</summary>
    public static int LaunchInSession(uint sessionId, string applicationPath, string commandLine, string workingDirectory)
    {
        IntPtr userToken;
        if (!WTSQueryUserToken(sessionId, out userToken))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "WTSQueryUserToken failed");

        IntPtr primaryToken = IntPtr.Zero;
        IntPtr environment = IntPtr.Zero;
        try
        {
            if (!DuplicateTokenEx(userToken, MAXIMUM_ALLOWED, IntPtr.Zero, SecurityImpersonation, TokenPrimary, out primaryToken))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "DuplicateTokenEx failed");
            if (!CreateEnvironmentBlock(out environment, primaryToken, false))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateEnvironmentBlock failed");

            STARTUPINFO si = new STARTUPINFO();
            si.cb = Marshal.SizeOf(typeof(STARTUPINFO));
            si.lpDesktop = "winsta0\\default";
            PROCESS_INFORMATION pi;
            // CreateProcessW may write to the command-line buffer, so it must be mutable.
            StringBuilder cmd = new StringBuilder(commandLine);
            if (!CreateProcessAsUser(primaryToken, applicationPath, cmd, IntPtr.Zero, IntPtr.Zero, false,
                    CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, environment, workingDirectory, ref si, out pi))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateProcessAsUser failed");

            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
            return pi.dwProcessId;
        }
        finally
        {
            if (environment != IntPtr.Zero) DestroyEnvironmentBlock(environment);
            if (primaryToken != IntPtr.Zero) CloseHandle(primaryToken);
            CloseHandle(userToken);
        }
    }
}

// Hears Windows shutting down, so the watchdog can tell the server "this PC is turning off" before it
// goes quiet (otherwise parents would get a "PC went silent" alert). A hidden top-level window on its own
// thread receives WM_QUERYENDSESSION / WM_ENDSESSION, which Windows sends to every process with a window.
// Plain Win32 (no SystemEvents) so it compiles under both Windows PowerShell 5.1 and PowerShell 7.
public static class KcShutdownWatcher
{
    private delegate IntPtr WndProcDelegate(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WNDCLASS
    {
        public uint style;
        public WndProcDelegate lpfnWndProc;
        public int cbClsExtra, cbWndExtra;
        public IntPtr hInstance, hIcon, hCursor, hbrBackground;
        public string lpszMenuName, lpszClassName;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MSG
    {
        public IntPtr hwnd;
        public uint message;
        public IntPtr wParam, lParam;
        public uint time;
        public int x, y;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern ushort RegisterClassW(ref WNDCLASS wc);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateWindowExW(uint exStyle, string className, string windowName, uint style,
        int x, int y, int width, int height, IntPtr parent, IntPtr menu, IntPtr instance, IntPtr param);

    [DllImport("user32.dll")]
    private static extern IntPtr DefWindowProcW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern int GetMessageW(out MSG msg, IntPtr hWnd, uint min, uint max);

    [DllImport("user32.dll")]
    private static extern bool TranslateMessage(ref MSG msg);

    [DllImport("user32.dll")]
    private static extern IntPtr DispatchMessageW(ref MSG msg);

    private const uint WM_QUERYENDSESSION = 0x0011;
    private const uint WM_ENDSESSION = 0x0016;

    private static WndProcDelegate proc;   // kept referenced so the GC doesn't collect it
    private static volatile bool ending;
    private static Thread thread;

    /// <summary>True once Windows has announced that it is shutting down or restarting.</summary>
    public static bool Ending { get { return ending; } }

    public static void Start()
    {
        if (thread != null) return;
        thread = new Thread(Run);
        thread.IsBackground = true;
        thread.Start();
    }

    private static IntPtr WndProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam)
    {
        if (msg == WM_QUERYENDSESSION)
        {
            ending = true;
            return new IntPtr(1);   // never block shutdown
        }
        if (msg == WM_ENDSESSION && wParam != IntPtr.Zero)
        {
            ending = true;
            Thread.Sleep(3000);     // give the watchdog's loop a moment to send its "shutting down" event
            return IntPtr.Zero;
        }
        return DefWindowProcW(hWnd, msg, wParam, lParam);
    }

    private static void Run()
    {
        try
        {
            proc = WndProc;
            WNDCLASS wc = new WNDCLASS();
            wc.lpfnWndProc = proc;
            wc.lpszClassName = "KidputerControlShutdownWatcher";
            if (RegisterClassW(ref wc) == 0) return;
            // A hidden top-level window (not message-only: those don't get the shutdown broadcast).
            IntPtr hwnd = CreateWindowExW(0, wc.lpszClassName, "Kidputer Control", 0, 0, 0, 0, 0,
                IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
            if (hwnd == IntPtr.Zero) return;
            MSG m;
            while (GetMessageW(out m, IntPtr.Zero, 0, 0) > 0)
            {
                TranslateMessage(ref m);
                DispatchMessageW(ref m);
            }
        }
        catch (Exception)
        {
            // Best effort: without it, a shutdown just looks like a silent PC until it's back.
        }
    }
}
