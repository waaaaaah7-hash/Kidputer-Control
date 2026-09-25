// Win32 interop for the SYSTEM watchdog, loaded with Add-Type.
// A SYSTEM process (session 0) can't show UI to the user, so the watchdog launches the UI agent into the
// active console session with the logged-on user's own token (WTSQueryUserToken needs SeTcbPrivilege,
// which SYSTEM has). The UI therefore runs with the child's normal, unprivileged rights.
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;

public static class SmNative
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
