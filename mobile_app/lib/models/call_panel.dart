import 'device.dart';

/// The panels a hub reply can pop open over the voice call.
enum CallPanelKind { files, screenshot, camera }

/// `"ui": {"panel": "files" | "screenshot" | "camera", "device_id", "path", "title"}` in a
/// hub reply (sendVoiceUtterance / sendCommand / late_reply result).
class CallPanelRequest {
  final CallPanelKind kind;
  final String? deviceId;
  final String? path;
  final String? title;

  /// Bumped for every request, so asking for the same panel again re-applies it.
  final int serial;

  static int _counter = 0;

  CallPanelRequest(this.kind, {this.deviceId, this.path, this.title}) : serial = ++_counter;

  static CallPanelKind? kindFromName(Object? name) {
    switch (name?.toString().trim().toLowerCase()) {
      case 'files':
      case 'file':
      case 'explorer':
        return CallPanelKind.files;
      case 'screenshot':
      case 'screen':
        return CallPanelKind.screenshot;
      case 'camera':
      case 'photo':
        return CallPanelKind.camera;
      default:
        return null;
    }
  }

  /// Reads the `ui` block of a hub reply; null when there is none (or it names no known panel).
  static CallPanelRequest? fromReply(Map<String, dynamic>? res) {
    final ui = res?['ui'];
    if (ui is! Map) return null;
    final kind = kindFromName(ui['panel']);
    if (kind == null) return null;
    return CallPanelRequest(
      kind,
      deviceId: _text(ui['device_id']),
      path: _text(ui['path']),
      title: _text(ui['title']),
    );
  }

  static String? _text(Object? v) {
    if (v == null) return null;
    final s = v.toString().trim();
    return s.isEmpty || s == 'null' ? null : s;
  }
}

/// The PC a panel works on: the one the hub named (when it is online), else the first online PC.
/// Null means no PC is online. The Linux server is used only when the hub names it and the
/// panel can work on it ([allowServer]: the files panel); it is never picked as "the PC".
WillyDevice? pickPanelPc(List<WillyDevice> devices, String? deviceId, {bool allowServer = false}) {
  if (deviceId != null) {
    for (final d in devices) {
      if (d.id != deviceId) continue;
      if (d.isServer && !allowServer) break;
      return d.online ? d : null;
    }
  }
  for (final d in devices) {
    if (d.isPc && d.online) return d;
  }
  return null;
}

// ------------------------------------------------------------------ PC files

/// One row of the PC's `list_dir` answer.
class PcEntry {
  final String name;
  final String path;
  final bool folder;
  final int? size;
  final double? modified; // epoch seconds

  const PcEntry({required this.name, required this.path, required this.folder, this.size, this.modified});

  static PcEntry? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final path = raw['path']?.toString() ?? '';
    if (path.isEmpty) return null;
    final name = raw['name']?.toString().trim();
    final size = asInt(raw['size']);
    var modified = asDouble(raw['modified']);
    if (modified != null && modified > 1e12) modified = modified / 1000; // milliseconds
    return PcEntry(
      name: (name == null || name.isEmpty) ? baseName(path) : name,
      path: path,
      folder: raw['folder'] == true,
      size: size,
      modified: modified,
    );
  }

  String get extension {
    if (folder) return '';
    final dot = name.lastIndexOf('.');
    return dot <= 0 || dot == name.length - 1 ? '' : name.substring(dot + 1).toLowerCase();
  }
}

/// A drive in the `drives` list of `list_dir`.
class PcDrive {
  final String name; // "C:\"
  final String label;
  final double? freeGb;
  final double? totalGb;

  const PcDrive({required this.name, this.label = '', this.freeGb, this.totalGb});

  static PcDrive? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final name = raw['name']?.toString() ?? '';
    if (name.isEmpty) return null;
    return PcDrive(
      name: name,
      label: raw['label']?.toString() ?? '',
      freeGb: asDouble(raw['free_gb']),
      totalGb: asDouble(raw['total_gb']),
    );
  }

  /// "C:" for "C:\"; a Linux mount point keeps its path ("/", "/mnt/data").
  String get letter {
    if (name.length > 1 && (name.endsWith('\\') || name.endsWith('/'))) return name.substring(0, name.length - 1);
    return name;
  }

  /// A Linux mount point rather than a Windows drive.
  bool get isMount => name.startsWith('/');
}

/// The drive (or mount point) [path] is on: the longest one that contains it.
PcDrive? driveOf(String path, List<PcDrive> drives) {
  final p = path.toLowerCase();
  if (p.isEmpty) return null;
  PcDrive? best;
  for (final d in drives) {
    final key = d.letter.toLowerCase();
    if (key.isEmpty || !p.startsWith(key)) continue;
    final rest = p.substring(key.length);
    final whole = rest.isEmpty ||
        key.endsWith('/') ||
        key.endsWith('\\') ||
        rest.startsWith('/') ||
        rest.startsWith('\\');
    if (whole && (best == null || key.length > best.letter.length)) best = d;
  }
  return best;
}

/// A parsed `list_dir` answer. An empty [path] is the top level (drives + home folders).
class DirListing {
  final String path;
  final String? parent;
  final List<PcEntry> entries;
  final List<PcDrive> drives;

  const DirListing({required this.path, this.parent, this.entries = const [], this.drives = const []});

  bool get isRoot => path.isEmpty;

  static DirListing fromResult(Map<String, dynamic> res, {String requested = ''}) {
    final parent = res['parent']?.toString();
    return DirListing(
      path: res['path']?.toString() ?? requested,
      parent: (parent == null || parent.isEmpty) ? null : parent,
      entries: sortEntries((res['entries'] as List? ?? const []).map(PcEntry.fromJson).whereType<PcEntry>()),
      drives: (res['drives'] as List? ?? const []).map(PcDrive.fromJson).whereType<PcDrive>().toList(),
    );
  }
}

/// Folders first, then by name (case-insensitive).
List<PcEntry> sortEntries(Iterable<PcEntry> entries) {
  final list = entries.toList();
  list.sort((a, b) {
    if (a.folder != b.folder) return a.folder ? -1 : 1;
    return a.name.toLowerCase().compareTo(b.name.toLowerCase());
  });
  return list;
}

String _sepOf(String path) => path.contains('\\') || !path.contains('/') ? '\\' : '/';

/// `D:\Work` + `New` -> `D:\Work\New`; `D:\` + `New` -> `D:\New`.
String joinPcPath(String folder, String name) {
  final sep = _sepOf(folder);
  if (folder.isEmpty) return name;
  return folder.endsWith('\\') || folder.endsWith('/') ? '$folder$name' : '$folder$sep$name';
}

/// Last segment of a Windows (or POSIX) path; a drive root stays as it is.
String baseName(String path) {
  var p = path;
  while (p.length > 1 && (p.endsWith('\\') || p.endsWith('/'))) {
    if (RegExp(r'^[A-Za-z]:[\\/]$').hasMatch(p)) return p;
    p = p.substring(0, p.length - 1);
  }
  final i = p.lastIndexOf(RegExp(r'[\\/]'));
  return i < 0 ? p : p.substring(i + 1);
}

/// One tappable step of the breadcrumb bar.
class PathCrumb {
  final String label;
  final String path;

  const PathCrumb(this.label, this.path);

  @override
  bool operator ==(Object other) => other is PathCrumb && other.label == label && other.path == path;

  @override
  int get hashCode => Object.hash(label, path);

  @override
  String toString() => 'PathCrumb($label, $path)';
}

/// `D:\Work\Sub` -> [D:\ , Work, Sub] with the full path of each step.
/// UNC paths (`\\server\share\x`) start with `\\server\share`.
List<PathCrumb> breadcrumbsFor(String? path) {
  final p = path?.trim() ?? '';
  if (p.isEmpty) return const [];
  final sep = _sepOf(p);
  final crumbs = <PathCrumb>[];
  String current;
  String rest;
  final drive = RegExp(r'^([A-Za-z]:)[\\/]?').firstMatch(p);
  final unc = RegExp(r'^(\\\\[^\\]+\\[^\\]+)\\?').firstMatch(p);
  if (drive != null) {
    current = '${drive.group(1)}$sep';
    crumbs.add(PathCrumb(drive.group(1)!, current));
    rest = p.substring(drive.end);
  } else if (unc != null) {
    current = unc.group(1)!;
    crumbs.add(PathCrumb(current, current));
    rest = p.substring(unc.end);
  } else if (p.startsWith('/')) {
    current = '/';
    crumbs.add(const PathCrumb('/', '/'));
    rest = p.substring(1);
  } else {
    current = '';
    rest = p;
  }
  for (final part in rest.split(RegExp(r'[\\/]'))) {
    if (part.isEmpty) continue;
    current = current.isEmpty ? part : joinPcPath(current, part);
    crumbs.add(PathCrumb(part, current));
  }
  return crumbs;
}

/// A new name that Windows accepts: no path separators or reserved characters.
String? validPcFileName(String raw) {
  final name = raw.trim();
  if (name.isEmpty || name == '.' || name == '..') return null;
  if (RegExp(r'[\\/:*?"<>|\x00-\x1F]').hasMatch(name)) return null;
  if (name.endsWith('.') || name.endsWith(' ')) return null;
  return name;
}

/// Coarse file type for picking an icon.
enum PcFileType { folder, image, video, audio, pdf, document, sheet, slides, archive, code, app, text, other }

PcFileType fileTypeOf(PcEntry e) {
  if (e.folder) return PcFileType.folder;
  switch (e.extension) {
    case 'jpg':
    case 'jpeg':
    case 'png':
    case 'gif':
    case 'bmp':
    case 'webp':
    case 'heic':
    case 'svg':
    case 'ico':
    case 'tif':
    case 'tiff':
      return PcFileType.image;
    case 'mp4':
    case 'mkv':
    case 'avi':
    case 'mov':
    case 'wmv':
    case 'webm':
    case 'flv':
    case 'm4v':
      return PcFileType.video;
    case 'mp3':
    case 'wav':
    case 'flac':
    case 'aac':
    case 'm4a':
    case 'ogg':
    case 'wma':
    case 'opus':
      return PcFileType.audio;
    case 'pdf':
      return PcFileType.pdf;
    case 'doc':
    case 'docx':
    case 'odt':
    case 'rtf':
      return PcFileType.document;
    case 'xls':
    case 'xlsx':
    case 'csv':
    case 'ods':
      return PcFileType.sheet;
    case 'ppt':
    case 'pptx':
    case 'odp':
      return PcFileType.slides;
    case 'zip':
    case 'rar':
    case '7z':
    case 'tar':
    case 'gz':
    case 'bz2':
    case 'xz':
    case 'iso':
      return PcFileType.archive;
    case 'py':
    case 'js':
    case 'ts':
    case 'dart':
    case 'java':
    case 'kt':
    case 'c':
    case 'cpp':
    case 'h':
    case 'cs':
    case 'go':
    case 'rs':
    case 'html':
    case 'css':
    case 'json':
    case 'xml':
    case 'yaml':
    case 'yml':
    case 'sh':
    case 'ps1':
    case 'bat':
    case 'sql':
      return PcFileType.code;
    case 'exe':
    case 'msi':
    case 'apk':
    case 'lnk':
    case 'appx':
      return PcFileType.app;
    case 'txt':
    case 'md':
    case 'log':
    case 'ini':
    case 'cfg':
      return PcFileType.text;
    default:
      return PcFileType.other;
  }
}
