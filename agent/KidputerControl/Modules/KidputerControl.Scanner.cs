// Folder-size scanner for roadmap #19 (like TreeSize): totals for every folder on a drive, the largest
// files, and space by file type. Names, sizes and dates only: it never opens a file.
//
// Uses FindFirstFileEx with the "basic info, large fetch" options, which is much faster than .NET's
// directory APIs. Folder links (junctions, symlinks) are not followed, so nothing is counted twice and
// nothing loops. OneDrive "online-only" files count towards size but not towards size on disk.
// Writes its own JSON (PowerShell's ConvertTo-Json is far too slow for a tree this size).
// C# 5 so Windows PowerShell 5.1 can compile it.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

public sealed class KcScanNode
{
    public string Name;
    public long Size;
    public long Alloc;
    public long Files;
    public long Folders;
    public long Modified;      // unix seconds, newest file or folder inside
    public bool Inaccessible;
    public List<KcScanNode> Children = new List<KcScanNode>();
}

public static class KcScanner
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WIN32_FIND_DATA
    {
        public uint dwFileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME ftCreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME ftLastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME ftLastWriteTime;
        public uint nFileSizeHigh;
        public uint nFileSizeLow;
        public uint dwReserved0;
        public uint dwReserved1;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string cFileName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 14)] public string cAlternateFileName;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr FindFirstFileExW(string fileName, int infoLevel, out WIN32_FIND_DATA data, int searchOp, IntPtr filter, int flags);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool FindNextFileW(IntPtr handle, out WIN32_FIND_DATA data);

    [DllImport("kernel32.dll")]
    private static extern bool FindClose(IntPtr handle);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool GetDiskFreeSpaceW(string root, out uint sectorsPerCluster, out uint bytesPerSector, out uint freeClusters, out uint totalClusters);

    private const int FindExInfoBasic = 1;
    private const int FIND_FIRST_EX_LARGE_FETCH = 2;
    private static readonly IntPtr INVALID = new IntPtr(-1);
    private const uint ATTR_DIRECTORY = 0x10;
    private const uint ATTR_REPARSE = 0x400;
    private const uint ATTR_OFFLINE = 0x1000;
    private const uint ATTR_RECALL_ON_OPEN = 0x40000;
    private const uint ATTR_RECALL_ON_DATA_ACCESS = 0x400000;

    [DllImport("kernel32.dll")]
    private static extern IntPtr GetCurrentProcess();

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetPriorityClass(IntPtr process, uint priorityClass);

    private const uint PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000;

    /// <summary>Lowest CPU, disk and memory priority (Windows' background mode), so games don't stutter.</summary>
    public static bool EnterBackgroundMode()
    {
        if (!OnWindows) return false;
        return SetPriorityClass(GetCurrentProcess(), PROCESS_MODE_BACKGROUND_BEGIN);
    }

    // Progress, readable from another thread while a scan runs.
    public static long FilesScanned;
    public static long FoldersScanned;

    private sealed class Big { public string Path; public long Size; public long Modified; }

    private struct Entry { public string Name; public uint Attributes; public long Size; public long Modified; }

    private static readonly bool OnWindows = Environment.OSVersion.Platform == PlatformID.Win32NT;

    /// <summary>One folder's entries, or null if it can't be read. Windows: FindFirstFileEx (fast, and the
    /// \\?\ prefix allows paths longer than 260 characters). Elsewhere (the tests): .NET's own listing.</summary>
    private static List<Entry> List(string path)
    {
        List<Entry> list = new List<Entry>();
        if (!OnWindows)
        {
            try
            {
                foreach (FileSystemInfo fi in new DirectoryInfo(path).EnumerateFileSystemInfos())
                {
                    Entry e = new Entry();
                    e.Name = fi.Name;
                    e.Attributes = (uint)fi.Attributes;
                    FileInfo f = fi as FileInfo;
                    e.Size = f != null ? f.Length : 0;
                    e.Modified = (long)(fi.LastWriteTimeUtc - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
                    list.Add(e);
                }
            }
            catch (UnauthorizedAccessException) { return null; }
            catch (IOException) { return null; }
            return list;
        }
        string query = (path.StartsWith("\\\\") ? path : "\\\\?\\" + path) + "*";
        WIN32_FIND_DATA d;
        IntPtr h = FindFirstFileExW(query, FindExInfoBasic, out d, 0, IntPtr.Zero, FIND_FIRST_EX_LARGE_FETCH);
        if (h == INVALID)
        {
            int err = Marshal.GetLastWin32Error();
            return (err == 2 || err == 18) ? list : null;   // "no files" is an empty folder, anything else unreadable
        }
        try
        {
            do
            {
                if (d.cFileName == "." || d.cFileName == "..") continue;
                Entry e = new Entry();
                e.Name = d.cFileName;
                e.Attributes = d.dwFileAttributes;
                e.Size = ((long)d.nFileSizeHigh << 32) | d.nFileSizeLow;
                e.Modified = ToUnix(d.ftLastWriteTime);
                list.Add(e);
            } while (FindNextFileW(h, out d));
        }
        finally
        {
            FindClose(h);
        }
        return list;
    }

    private sealed class State
    {
        public long Cluster = 4096;
        public List<Big> Largest = new List<Big>();
        public long LargestMin;
        public Dictionary<string, long[]> ByType = new Dictionary<string, long[]>();
        public long Inaccessible;
        public string ProgressPath;
        public DateTime NextProgress = DateTime.UtcNow;
        public string Root;
    }

    private static readonly Dictionary<string, string> TypeOf = BuildTypes();

    private static Dictionary<string, string> BuildTypes()
    {
        Dictionary<string, string> t = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        Add(t, "video", ".mp4 .mkv .avi .mov .wmv .webm .m4v .flv .mpg .mpeg .ts");
        Add(t, "music", ".mp3 .wav .flac .m4a .aac .ogg .wma .opus");
        Add(t, "pictures", ".jpg .jpeg .png .gif .bmp .webp .heic .tif .tiff .raw .psd .svg");
        Add(t, "archives", ".zip .rar .7z .tar .gz .tgz .cab .iso .img");
        Add(t, "programs", ".exe .msi .dll .sys .appx .msix .jar");
        Add(t, "game data", ".pak .vpk .bsa .ba2 .bundle .assets .ucas .utoc .wad .vdf .sav .mca .esm .esp");
        Add(t, "documents", ".pdf .doc .docx .xls .xlsx .ppt .pptx .txt .odt .ods .rtf .md .csv .epub");
        return t;
    }

    private static void Add(Dictionary<string, string> t, string kind, string exts)
    {
        foreach (string e in exts.Split(' ')) t[e] = kind;
    }

    /// <summary>Scan a drive or folder and return the result as JSON (see Write for the shape).</summary>
    /// <param name="minNodeBytes">Folders smaller than this (below the first two levels) are folded into
    /// their parent's totals, which keeps the upload small.</param>
    public static string ScanToJson(string root, string progressPath, long minNodeBytes, int maxDepth)
    {
        FilesScanned = 0;
        FoldersScanned = 0;
        State st = new State();
        st.ProgressPath = progressPath;
        st.Root = root;
        uint spc, bps, fc, tc;
        string driveRoot = Path.GetPathRoot(root);
        if (OnWindows && !string.IsNullOrEmpty(driveRoot) && GetDiskFreeSpaceW(driveRoot, out spc, out bps, out fc, out tc)) st.Cluster = (long)spc * bps;
        DateTime started = DateTime.UtcNow;
        string sep = Path.DirectorySeparatorChar.ToString();
        KcScanNode top = ScanFolder(root.EndsWith(sep) ? root : root + sep, root, st, 0, minNodeBytes, maxDepth);
        StringBuilder sb = new StringBuilder(1 << 20);
        sb.Append("{\"root\":"); Str(sb, root);
        sb.Append(",\"started_at\":"); Str(sb, started.ToString("o", CultureInfo.InvariantCulture));
        sb.Append(",\"finished_at\":"); Str(sb, DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture));
        sb.Append(",\"cluster\":").Append(st.Cluster);
        sb.Append(",\"inaccessible\":").Append(st.Inaccessible);
        sb.Append(",\"tree\":"); Write(sb, top);
        sb.Append(",\"largest\":[");
        st.Largest.Sort(delegate (Big a, Big b) { return b.Size.CompareTo(a.Size); });
        for (int i = 0; i < st.Largest.Count; i++)
        {
            if (i > 0) sb.Append(',');
            sb.Append("{\"p\":"); Str(sb, st.Largest[i].Path);
            sb.Append(",\"s\":").Append(st.Largest[i].Size).Append(",\"m\":").Append(st.Largest[i].Modified).Append('}');
        }
        sb.Append("],\"by_type\":{");
        bool first = true;
        foreach (KeyValuePair<string, long[]> kv in st.ByType)
        {
            if (!first) sb.Append(',');
            first = false;
            Str(sb, kv.Key);
            sb.Append(":{\"s\":").Append(kv.Value[0]).Append(",\"f\":").Append(kv.Value[1]).Append('}');
        }
        sb.Append("}}");
        WriteProgress(st, true);
        return sb.ToString();
    }

    private static KcScanNode ScanFolder(string path, string name, State st, int depth, long minNodeBytes, int maxDepth)
    {
        KcScanNode node = new KcScanNode();
        node.Name = name;
        FoldersScanned++;
        List<Entry> entries = List(path);
        if (entries == null)
        {
            node.Inaccessible = true;
            st.Inaccessible++;
            return node;
        }
        foreach (Entry e in entries)
        {
            if (e.Modified > node.Modified) node.Modified = e.Modified;
            if ((e.Attributes & ATTR_DIRECTORY) != 0)
            {
                if ((e.Attributes & ATTR_REPARSE) != 0) continue;   // junction or symlink: don't follow
                KcScanNode child = ScanFolder(path + e.Name + Path.DirectorySeparatorChar, e.Name, st, depth + 1, minNodeBytes, maxDepth);
                node.Size += child.Size;
                node.Alloc += child.Alloc;
                node.Files += child.Files;
                node.Folders += child.Folders + 1;
                if (child.Modified > node.Modified) node.Modified = child.Modified;
                // Keep the first two levels, big folders deeper down, and unreadable ones (so they show).
                if (child.Inaccessible || depth < 2 || (depth < maxDepth && child.Size >= minNodeBytes)) node.Children.Add(child);
            }
            else
            {
                if ((e.Attributes & ATTR_REPARSE) != 0) continue;   // file symlink: its target is counted where it lives
                bool online = (e.Attributes & (ATTR_OFFLINE | ATTR_RECALL_ON_OPEN | ATTR_RECALL_ON_DATA_ACCESS)) != 0;
                long alloc = online ? 0 : (e.Size + st.Cluster - 1) / st.Cluster * st.Cluster;
                node.Size += e.Size;
                node.Alloc += alloc;
                node.Files++;
                FilesScanned++;
                Track(st, path + e.Name, e.Size, e.Modified);
                string kind;
                string ext = Path.GetExtension(e.Name);
                if (ext == null || !TypeOf.TryGetValue(ext, out kind)) kind = "other";
                long[] acc;
                if (!st.ByType.TryGetValue(kind, out acc)) { acc = new long[2]; st.ByType[kind] = acc; }
                acc[0] += e.Size;
                acc[1]++;
            }
            if (DateTime.UtcNow >= st.NextProgress) WriteProgress(st, false);
        }
        node.Children.Sort(delegate (KcScanNode a, KcScanNode b) { return b.Size.CompareTo(a.Size); });
        return node;
    }

    private static void Track(State st, string path, long size, long modified)
    {
        if (st.Largest.Count >= 100 && size <= st.LargestMin) return;
        Big b = new Big();
        b.Path = path;
        b.Size = size;
        b.Modified = modified;
        st.Largest.Add(b);
        if (st.Largest.Count > 100)
        {
            int minAt = 0;
            for (int i = 1; i < st.Largest.Count; i++) if (st.Largest[i].Size < st.Largest[minAt].Size) minAt = i;
            st.Largest.RemoveAt(minAt);
        }
        if (st.Largest.Count >= 100)
        {
            long min = long.MaxValue;
            foreach (Big x in st.Largest) if (x.Size < min) min = x.Size;
            st.LargestMin = min;
        }
    }

    private static void WriteProgress(State st, bool done)
    {
        st.NextProgress = DateTime.UtcNow.AddSeconds(2);
        if (string.IsNullOrEmpty(st.ProgressPath)) return;
        try
        {
            File.WriteAllText(st.ProgressPath, "{\"files\":" + FilesScanned + ",\"folders\":" + FoldersScanned + ",\"done\":" + (done ? "true" : "false") + "}");
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private static long ToUnix(System.Runtime.InteropServices.ComTypes.FILETIME ft)
    {
        long ticks = ((long)(uint)ft.dwHighDateTime << 32) | (uint)ft.dwLowDateTime;
        if (ticks <= 0) return 0;
        try { return (long)(DateTime.FromFileTimeUtc(ticks) - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds; }
        catch (ArgumentOutOfRangeException) { return 0; }
    }

    // Node: {"n":name,"s":size,"a":size on disk,"f":files,"d":folders,"m":modified,"x":inaccessible,"c":[children]}
    private static void Write(StringBuilder sb, KcScanNode n)
    {
        sb.Append("{\"n\":"); Str(sb, n.Name);
        sb.Append(",\"s\":").Append(n.Size).Append(",\"a\":").Append(n.Alloc).Append(",\"f\":").Append(n.Files)
          .Append(",\"d\":").Append(n.Folders).Append(",\"m\":").Append(n.Modified);
        if (n.Inaccessible) sb.Append(",\"x\":true");
        if (n.Children.Count > 0)
        {
            sb.Append(",\"c\":[");
            for (int i = 0; i < n.Children.Count; i++)
            {
                if (i > 0) sb.Append(',');
                Write(sb, n.Children[i]);
            }
            sb.Append(']');
        }
        sb.Append('}');
    }

    private static void Str(StringBuilder sb, string s)
    {
        sb.Append('"');
        foreach (char c in s ?? "")
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                default:
                    if (c < 0x20 || char.IsSurrogate(c)) sb.Append("\\u").Append(((int)c).ToString("x4"));
                    else sb.Append(c);
                    break;
            }
        }
        sb.Append('"');
    }
}
