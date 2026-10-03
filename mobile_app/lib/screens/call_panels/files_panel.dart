import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';

import '../../models/call_panel.dart';
import '../../models/device.dart';
import '../../services/api_service.dart';
import '../../services/file_transfer_service.dart';
import '../../theme.dart';
import '../../widgets/common.dart';
import '../server/server_text_file.dart';
import 'panel_host.dart';

/// Asks the PC for a folder listing. Returns the listing or an error text.
Future<(DirListing?, String?)> listPcDir(String pcId, String path) async {
  final res = await ApiService.deviceAction(pcId, 'list_dir', {'path': path}, const Duration(seconds: 20));
  if (res['success'] == true) return (DirListing.fromResult(res, requested: path), null);
  return (null, panelErrorText(res, "Couldn't open that folder."));
}

(IconData, Color) fileIconOf(PcEntry e) => switch (fileTypeOf(e)) {
      PcFileType.folder => (Icons.folder_rounded, WillyColors.amber),
      PcFileType.image => (Icons.image_rounded, WillyColors.pink),
      PcFileType.video => (Icons.movie_rounded, WillyColors.purple),
      PcFileType.audio => (Icons.music_note_rounded, WillyColors.green),
      PcFileType.pdf => (Icons.picture_as_pdf_rounded, WillyColors.red),
      PcFileType.document => (Icons.description_rounded, WillyColors.blue),
      PcFileType.sheet => (Icons.table_chart_rounded, WillyColors.green),
      PcFileType.slides => (Icons.slideshow_rounded, WillyColors.amber),
      PcFileType.archive => (Icons.folder_zip_rounded, WillyColors.sky),
      PcFileType.code => (Icons.code_rounded, WillyColors.cyan),
      PcFileType.app => (Icons.apps_rounded, WillyColors.violet),
      PcFileType.text => (Icons.article_rounded, WillyColors.textSoft),
      PcFileType.other => (Icons.insert_drive_file_rounded, WillyColors.muted),
    };

/// The top of the folder tree: "This PC", or the server's name.
String rootLabel(WillyDevice device) => device.isServer ? device.name : 'This PC';

IconData rootIcon(WillyDevice device) => device.isServer ? Icons.dns_rounded : Icons.computer_rounded;

String formatModified(double? epochSeconds) {
  if (epochSeconds == null || epochSeconds <= 0) return '';
  final d = DateTime.fromMillisecondsSinceEpoch((epochSeconds * 1000).round());
  final now = DateTime.now();
  if (d.year == now.year && d.month == now.month && d.day == now.day) return 'Today ${DateFormat('HH:mm').format(d)}';
  if (d.year == now.year) return DateFormat('d MMM, HH:mm').format(d);
  return DateFormat('d MMM y').format(d);
}

/// Browse and manage the files on the user's PC, or on the Willy server (device_type
/// "server": Linux paths, deletes go to ~/.willy-trash, text files can be viewed and edited).
///
/// Over a call it lives in the panel sheet ([chrome] set). Without [chrome] it is embedded
/// in a page (the Server screen's Files tab): own scrolling, no call header.
class FilesPanel extends StatefulWidget {
  final WillyDevice pc;
  final CallPanelRequest request;
  final PanelChrome? chrome;

  /// False while the panel sits in a hidden tab: Back then leaves the page as usual.
  final bool handlesBack;

  const FilesPanel({super.key, required this.pc, required this.request, this.chrome, this.handlesBack = true});

  @override
  State<FilesPanel> createState() => _FilesPanelState();
}

class _FilesPanelState extends State<FilesPanel> {
  DirListing _dir = const DirListing(path: '');
  List<PcDrive> _drives = const [];
  bool _loading = true;
  String? _error;
  int _serial = 0;
  String _requested = ''; // the folder last asked for (retried after an error)
  final _filter = TextEditingController();
  String _query = '';
  final Set<String> _selected = {};
  bool _busy = false;

  ScrollController? _ownScroll;

  bool get _selecting => _selected.isNotEmpty;
  bool get _server => widget.pc.isServer;
  ScrollController get _scroll => widget.chrome?.scroll ?? (_ownScroll ??= ScrollController());

  @override
  void initState() {
    super.initState();
    _load(widget.request.path ?? '');
  }

  @override
  void didUpdateWidget(FilesPanel old) {
    super.didUpdateWidget(old);
    // Asked again by voice ("open my Downloads"): go there.
    if (widget.request.serial != old.request.serial && widget.request.path != null) _load(widget.request.path!);
  }

  @override
  void dispose() {
    _filter.dispose();
    _ownScroll?.dispose();
    super.dispose();
  }

  Future<void> _load(String path) async {
    final serial = ++_serial;
    _requested = path;
    setState(() {
      _loading = true;
      _error = null;
    });
    final (dir, error) = await listPcDir(widget.pc.id, path);
    if (!mounted || serial != _serial) return;
    setState(() {
      _loading = false;
      if (dir == null) {
        _error = error;
        return;
      }
      _dir = dir;
      if (dir.drives.isNotEmpty) _drives = dir.drives;
      _selected.clear();
      _filter.clear();
      _query = '';
    });
    if (dir != null && _scroll.hasClients) _scroll.jumpTo(0);
  }

  void _up() {
    if (_dir.isRoot) return;
    _load(_dir.parent ?? '');
  }

  List<PcEntry> get _visible {
    final q = _query.trim().toLowerCase();
    if (q.isEmpty) return _dir.entries;
    return _dir.entries.where((e) => e.name.toLowerCase().contains(q)).toList();
  }

  List<PcEntry> get _selectedEntries => _dir.entries.where((e) => _selected.contains(e.path)).toList();

  // ------------------------------------------------------------- actions

  Future<Map<String, dynamic>> _act(String action, Map<String, dynamic> payload) =>
      ApiService.deviceAction(widget.pc.id, action, payload, const Duration(seconds: 45));

  void _snack(Map<String, dynamic> res, {String ok = 'Done.', String failed = "That didn't work."}) {
    final (text, success) = panelResultText(res, ok: ok, failed: failed);
    showWillySnack(context, text, error: !success);
  }

  Future<void> _single(String action, Map<String, dynamic> payload, {bool reload = true, String ok = 'Done.'}) async {
    setState(() => _busy = true);
    final res = await _act(action, payload);
    if (!mounted) return;
    setState(() => _busy = false);
    _snack(res, ok: ok);
    if (reload && res['success'] == true) _load(_dir.path);
  }

  /// Runs [action] for each entry in turn; one summary snackbar at the end.
  Future<void> _many(List<PcEntry> items, String verb, Map<String, dynamic> Function(PcEntry) payload,
      {String action = 'manage_file', bool reload = true}) async {
    if (items.isEmpty) return;
    if (items.length == 1) {
      await _single(action, payload(items.first), reload: reload, ok: '$verb ${items.first.name}.');
      return;
    }
    setState(() => _busy = true);
    var done = 0;
    String? firstError;
    for (final e in items) {
      final res = await _act(action, payload(e));
      if (!mounted) return;
      if (res['success'] == true) {
        done++;
      } else {
        firstError ??= '${e.name}: ${panelErrorText(res, "didn't work")}';
      }
    }
    setState(() => _busy = false);
    showWillySnack(
      context,
      firstError == null ? '$verb ${items.length} items.' : '$verb $done of ${items.length}. $firstError',
      error: firstError != null,
    );
    if (reload) _load(_dir.path);
  }

  Future<void> _newFolder() async {
    final name = await askPcName(context, title: 'New folder', action: 'Create', initial: 'New folder');
    if (name == null) return;
    await _single('manage_file', {'op': 'mkdir', 'path': joinPcPath(_dir.path, name)}, ok: 'Created $name.');
  }

  Future<void> _rename(PcEntry e) async {
    final name = await askPcName(context, title: 'Rename', action: 'Rename', initial: e.name, selectStem: !e.folder);
    if (name == null || name == e.name) return;
    await _single('manage_file', {'op': 'rename', 'path': e.path, 'destination': name}, ok: 'Renamed to $name.');
  }

  Future<void> _moveOrCopy(List<PcEntry> items, {required bool move}) async {
    if (items.isEmpty) return;
    final dest = await Navigator.of(context).push<String>(MaterialPageRoute(
      fullscreenDialog: true,
      builder: (_) =>
          PcFolderPicker(pc: widget.pc, startPath: _dir.path, verb: move ? 'Move' : 'Copy', count: items.length),
    ));
    if (dest == null || !mounted) return;
    final todo = move ? items.where((e) => e.path != dest && _dir.path != dest).toList() : items;
    if (todo.isEmpty) {
      showWillySnack(context, 'Already in that folder.', error: true);
      return;
    }
    await _many(
        todo, move ? 'Moved' : 'Copied', (e) => {'op': move ? 'move' : 'copy', 'path': e.path, 'destination': dest});
  }

  Future<void> _delete(List<PcEntry> items) async {
    if (items.isEmpty) return;
    final pcName = widget.pc.name;
    final bin = _server ? '~/.willy-trash (restore it from there)' : 'the Recycle Bin';
    final names = items.take(5).map((e) => '• ${e.name}').join('\n');
    final more = items.length > 5 ? '\n…and ${items.length - 5} more' : '';
    final ok = await confirmAction(
      context,
      title: items.length == 1 ? 'Delete "${items.first.name}"?' : 'Delete ${items.length} items?',
      message: items.length == 1
          ? '${items.first.folder ? 'This folder and everything in it' : 'It'} goes to $bin on $pcName.'
          : '$names$more\n\nThey go to $bin on $pcName.',
      confirmLabel: 'Delete',
    );
    if (!ok || !mounted) return;
    await _many(items, 'Deleted', (e) => {'op': 'delete', 'path': e.path});
  }

  Future<void> _sendToPhone(List<PcEntry> items) async {
    final files = items.where((e) => !e.folder).toList();
    if (files.isEmpty) {
      showWillySnack(context, 'Pick files, not folders, to send to the phone.', error: true);
      return;
    }
    if (_server) {
      await _saveFromServer(files);
      return;
    }
    await _many(files, 'Sending', (e) => {'path': e.path}, action: 'send_file_to_phone', reload: false);
  }

  /// Server files: the server uploads each one to the hub (`file_to_hub`), then this phone
  /// downloads it into Downloads/Willy. The token only travels in the Authorization header.
  Future<void> _saveFromServer(List<PcEntry> files) async {
    setState(() => _busy = true);
    var done = 0;
    String? firstError;
    for (final e in files) {
      final res = await _act('file_to_hub', {'path': e.path});
      if (!mounted) return;
      final file = res['file'];
      if (res['success'] != true || file is! Map || file['url'] == null) {
        firstError ??= '${e.name}: ${panelErrorText(res, "couldn't upload it")}';
        continue;
      }
      final got = await FileTransferService.receiveFromHub({
        'url': file['url'],
        'name': file['name'] ?? e.name,
        'size': file['size'] ?? e.size,
        'mime': file['mime'],
        'id': file['id'],
        'from': widget.pc.name,
      });
      if (!mounted) return;
      if (got['success'] == true) {
        done++;
      } else {
        firstError ??= '${e.name}: ${panelErrorText(got, "couldn't download it")}';
      }
    }
    setState(() => _busy = false);
    final String text;
    if (firstError == null) {
      text = files.length == 1
          ? 'Saved ${files.first.name} to Downloads/Willy.'
          : 'Saved ${files.length} files to Downloads/Willy.';
    } else {
      text = files.length == 1 ? firstError : 'Saved $done of ${files.length}. $firstError';
    }
    showWillySnack(context, text, error: firstError != null);
  }

  Future<void> _zip(PcEntry e) => _single('manage_file', {'op': 'zip', 'path': e.path}, ok: 'Zipped ${e.name}.');

  Future<void> _unzip(PcEntry e) =>
      _single('manage_file', {'op': 'unzip', 'path': e.path}, ok: 'Extracted ${e.name}.');

  Future<void> _view(PcEntry e) async {
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(
      builder: (_) => ServerTextFile(device: widget.pc, path: e.path, name: e.name),
    ));
    if (saved == true && mounted) _load(_dir.path);
  }

  void _toggle(PcEntry e) {
    HapticFeedback.selectionClick();
    setState(() => _selected.contains(e.path) ? _selected.remove(e.path) : _selected.add(e.path));
  }

  void _onTap(PcEntry e) {
    if (_selecting) {
      _toggle(e);
    } else if (e.folder) {
      _load(e.path);
    } else {
      _showActions(e);
    }
  }

  Future<void> _showActions(PcEntry e) async {
    final (icon, color) = fileIconOf(e);
    final details = [
      if (!e.folder && e.size != null) FileTransferService.formatBytes(e.size!),
      formatModified(e.modified),
    ].where((s) => s.isNotEmpty).join(' · ');
    final choice = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: WillyColors.card,
      showDragHandle: true,
      builder: (ctx) {
        Widget item(IconData i, String label, String value, {Color c = WillyColors.textSoft}) => ListTile(
              leading: Icon(i, color: c),
              title: Text(label, style: TextStyle(color: c)),
              onTap: () => Navigator.pop(ctx, value),
            );
        return SafeArea(
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                ListTile(
                  leading: Icon(icon, color: color, size: 30),
                  title: Text(e.name,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: WillyColors.text, fontWeight: FontWeight.w700)),
                  subtitle: details.isEmpty ? null : Text(details, style: const TextStyle(color: WillyColors.muted)),
                ),
                const Divider(height: 1, color: WillyColors.border),
                if (e.folder) item(Icons.folder_open_rounded, 'Open', 'browse', c: WillyColors.cyan),
                if (!_server) item(Icons.open_in_new_rounded, 'Open on ${widget.pc.name}', 'open'),
                if (_server && !e.folder) item(Icons.article_outlined, 'View / edit', 'view', c: WillyColors.cyan),
                if (!e.folder)
                  item(Icons.phone_android_rounded, _server ? 'Save to this phone' : 'Send to phone', 'phone',
                      c: WillyColors.cyan),
                item(Icons.drive_file_rename_outline_rounded, 'Rename', 'rename'),
                item(Icons.drive_file_move_rounded, 'Move to…', 'move'),
                item(Icons.file_copy_rounded, 'Copy to…', 'copy'),
                if (_server) item(Icons.folder_zip_outlined, 'Zip', 'zip'),
                if (_server && e.extension == 'zip') item(Icons.unarchive_outlined, 'Unzip here', 'unzip'),
                item(Icons.checklist_rounded, 'Select', 'select'),
                item(Icons.delete_outline_rounded, 'Delete', 'delete', c: WillyColors.red),
              ],
            ),
          ),
        );
      },
    );
    if (!mounted || choice == null) return;
    switch (choice) {
      case 'browse':
        _load(e.path);
        break;
      case 'open':
        _single('open_file', {'path': e.path}, reload: false, ok: 'Opened ${e.name} on ${widget.pc.name}.');
        break;
      case 'phone':
        _sendToPhone([e]);
        break;
      case 'view':
        _view(e);
        break;
      case 'zip':
        _zip(e);
        break;
      case 'unzip':
        _unzip(e);
        break;
      case 'rename':
        _rename(e);
        break;
      case 'move':
        _moveOrCopy([e], move: true);
        break;
      case 'copy':
        _moveOrCopy([e], move: false);
        break;
      case 'select':
        _toggle(e);
        break;
      case 'delete':
        _delete([e]);
        break;
    }
  }

  // ------------------------------------------------------------------ UI

  @override
  Widget build(BuildContext context) {
    final visible = _visible;
    final title = widget.request.title ?? 'Files';
    return PopScope(
      // Back goes up a folder (or leaves select mode) before it closes the sheet.
      canPop: !widget.handlesBack || (_dir.isRoot && !_selecting),
      onPopInvokedWithResult: (didPop, _) {
        if (didPop || !widget.handlesBack) return;
        if (_selecting) {
          setState(_selected.clear);
        } else {
          _up();
        }
      },
      child: CustomScrollView(
        controller: _scroll,
        slivers: [
          SliverToBoxAdapter(child: _header(title)),
          SliverPersistentHeader(
            pinned: true,
            delegate: _BarDelegate(
              height: _selecting ? 56 : 104 + (_drives.isEmpty ? 0 : 44),
              child: _selecting ? _selectionBar() : _navBar(),
            ),
          ),
          if (_busy || (_loading && _dir.entries.isNotEmpty))
            const SliverToBoxAdapter(child: LinearProgressIndicator(minHeight: 2, color: WillyColors.cyan)),
          ..._body(visible),
          const SliverToBoxAdapter(child: SizedBox(height: 24)),
        ],
      ),
    );
  }

  Widget _header(String title) {
    final actions = [
      if (!_dir.isRoot)
        IconButton(
          tooltip: 'New folder',
          onPressed: _busy ? null : _newFolder,
          icon: const Icon(Icons.create_new_folder_outlined, color: WillyColors.textSoft),
        ),
      IconButton(
        tooltip: 'Refresh',
        onPressed: () => _load(_error != null ? _requested : _dir.path),
        icon: const Icon(Icons.refresh_rounded, color: WillyColors.textSoft),
      ),
    ];
    final chrome = widget.chrome;
    if (chrome != null) {
      return PanelHeader(
        chrome: chrome,
        icon: Icons.folder_rounded,
        color: WillyColors.amber,
        title: title,
        subtitle: widget.pc.name,
        actions: actions,
      );
    }
    // Embedded in a page, which already names the device.
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 8, 0),
      child: Row(
        children: [
          Expanded(
            child: Text(
              _dir.isRoot ? (_server ? 'Places and disks' : 'Drives and folders') : baseName(_dir.path),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(color: WillyColors.muted, fontSize: 12.5, fontWeight: FontWeight.w600),
            ),
          ),
          ...actions,
        ],
      ),
    );
  }

  List<Widget> _body(List<PcEntry> visible) {
    if (_loading && _dir.entries.isEmpty && _error == null) {
      return const [
        SliverFillRemaining(
          hasScrollBody: false,
          child: Center(child: Padding(padding: EdgeInsets.all(32), child: CircularProgressIndicator())),
        ),
      ];
    }
    if (_error != null) {
      return [
        SliverFillRemaining(
          hasScrollBody: false,
          child: _Message(
            icon: Icons.error_outline_rounded,
            color: WillyColors.red,
            text: _error!,
            action: TextButton.icon(
              onPressed: () => _load(_requested),
              icon: const Icon(Icons.refresh_rounded),
              label: const Text('Try again'),
            ),
          ),
        ),
      ];
    }
    final slivers = <Widget>[];
    if (_dir.isRoot && _drives.isNotEmpty && _query.isEmpty) {
      slivers.add(SliverList.builder(
        itemCount: _drives.length,
        itemBuilder: (context, i) => _DriveTile(drive: _drives[i], onTap: () => _load(_drives[i].name)),
      ));
    }
    if (visible.isEmpty) {
      slivers.add(SliverFillRemaining(
        hasScrollBody: false,
        child: _Message(
          icon: _query.isEmpty ? Icons.folder_off_outlined : Icons.search_off_rounded,
          color: WillyColors.faint,
          text: _query.isEmpty
              ? (_dir.isRoot && _drives.isNotEmpty ? '' : 'This folder is empty')
              : 'Nothing matches "$_query"',
        ),
      ));
      return slivers;
    }
    slivers.add(SliverList.builder(
      itemCount: visible.length,
      itemBuilder: (context, i) {
        final e = visible[i];
        return _EntryTile(
          entry: e,
          selecting: _selecting,
          selected: _selected.contains(e.path),
          onTap: () => _onTap(e),
          onLongPress: () => _toggle(e),
          onMore: () => _showActions(e),
        );
      },
    ));
    return slivers;
  }

  Widget _navBar() {
    final crumbs = breadcrumbsFor(_dir.path);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          height: 40,
          child: Row(
            children: [
              IconButton(
                tooltip: 'Up',
                visualDensity: VisualDensity.compact,
                onPressed: _dir.isRoot ? null : _up,
                icon: const Icon(Icons.arrow_upward_rounded, size: 20),
              ),
              Expanded(
                child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  reverse: true,
                  padding: const EdgeInsets.only(right: 12),
                  child: Row(
                    children: [
                      _Crumb(
                          label: rootLabel(widget.pc),
                          icon: rootIcon(widget.pc),
                          current: crumbs.isEmpty,
                          onTap: () => _load('')),
                      for (var i = 0; i < crumbs.length; i++) ...[
                        const Icon(Icons.chevron_right_rounded, size: 16, color: WillyColors.faint),
                        _Crumb(
                          label: crumbs[i].label,
                          current: i == crumbs.length - 1,
                          onTap: () => _load(crumbs[i].path),
                        ),
                      ],
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
        if (_drives.isNotEmpty)
          SizedBox(
            height: 44,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              itemCount: _drives.length,
              separatorBuilder: (context, i) => const SizedBox(width: 8),
              itemBuilder: (context, i) {
                final d = _drives[i];
                final here = identical(driveOf(_dir.path, _drives), d);
                return ChoiceChip(
                  selected: here,
                  showCheckmark: false,
                  visualDensity: VisualDensity.compact,
                  avatar: Icon(Icons.storage_rounded, size: 16, color: here ? WillyColors.bg : WillyColors.sky),
                  label: Text(
                    d.freeGb != null ? '${d.letter}  ${d.freeGb!.toStringAsFixed(0)} GB free' : d.letter,
                    style: TextStyle(color: here ? WillyColors.bg : WillyColors.textSoft, fontSize: 12),
                  ),
                  selectedColor: WillyColors.sky,
                  backgroundColor: WillyColors.card,
                  side: const BorderSide(color: WillyColors.border),
                  onSelected: (_) => _load(d.name),
                );
              },
            ),
          ),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
          child: SizedBox(
            height: 44,
            child: TextField(
              controller: _filter,
              onChanged: (v) => setState(() => _query = v),
              style: const TextStyle(color: WillyColors.text, fontSize: 14),
              decoration: InputDecoration(
                isDense: true,
                hintText: _dir.isRoot ? 'Filter' : 'Filter ${baseName(_dir.path)}',
                prefixIcon: const Icon(Icons.search_rounded, size: 20, color: WillyColors.faint),
                suffixIcon: _query.isEmpty
                    ? null
                    : IconButton(
                        icon: const Icon(Icons.close_rounded, size: 18),
                        onPressed: () => setState(() {
                          _filter.clear();
                          _query = '';
                        }),
                      ),
                contentPadding: const EdgeInsets.symmetric(vertical: 10),
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _selectionBar() {
    final items = _selectedEntries;
    final allSelected = _visible.isNotEmpty && _visible.every((e) => _selected.contains(e.path));
    return Container(
      color: WillyColors.cyan.withValues(alpha: 0.08),
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: Row(
        children: [
          IconButton(
            tooltip: 'Cancel',
            onPressed: () => setState(_selected.clear),
            icon: const Icon(Icons.close_rounded),
          ),
          Text('${_selected.length} selected',
              style: const TextStyle(color: WillyColors.text, fontWeight: FontWeight.w700)),
          const Spacer(),
          IconButton(
            tooltip: allSelected ? 'Select none' : 'Select all',
            onPressed: () => setState(() {
              if (allSelected) {
                _selected.clear();
              } else {
                _selected.addAll(_visible.map((e) => e.path));
              }
            }),
            icon: Icon(allSelected ? Icons.deselect_rounded : Icons.select_all_rounded),
          ),
          IconButton(
            tooltip: 'Send to phone',
            onPressed: _busy ? null : () => _sendToPhone(items),
            icon: const Icon(Icons.phone_android_rounded, color: WillyColors.cyan),
          ),
          IconButton(
            tooltip: 'Move to…',
            onPressed: _busy ? null : () => _moveOrCopy(items, move: true),
            icon: const Icon(Icons.drive_file_move_rounded),
          ),
          IconButton(
            tooltip: 'Copy to…',
            onPressed: _busy ? null : () => _moveOrCopy(items, move: false),
            icon: const Icon(Icons.file_copy_rounded),
          ),
          IconButton(
            tooltip: 'Delete',
            onPressed: _busy ? null : () => _delete(items),
            icon: const Icon(Icons.delete_outline_rounded, color: WillyColors.red),
          ),
        ],
      ),
    );
  }
}

class _BarDelegate extends SliverPersistentHeaderDelegate {
  final double height;
  final Widget child;

  _BarDelegate({required this.height, required this.child});

  @override
  double get minExtent => height;

  @override
  double get maxExtent => height;

  @override
  Widget build(BuildContext context, double shrinkOffset, bool overlapsContent) {
    return Material(
      color: WillyColors.bgElevated,
      elevation: overlapsContent || shrinkOffset > 0 ? 2 : 0,
      child: SizedBox(height: height, child: child),
    );
  }

  @override
  bool shouldRebuild(covariant _BarDelegate old) => true;
}

class _Crumb extends StatelessWidget {
  final String label;
  final IconData? icon;
  final bool current;
  final VoidCallback onTap;

  const _Crumb({required this.label, required this.current, required this.onTap, this.icon});

  @override
  Widget build(BuildContext context) {
    final color = current ? WillyColors.cyan : WillyColors.muted;
    return InkWell(
      borderRadius: BorderRadius.circular(8),
      onTap: current ? null : onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (icon != null) ...[Icon(icon, size: 15, color: color), const SizedBox(width: 4)],
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 160),
              child: Text(label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style:
                      TextStyle(color: color, fontSize: 13, fontWeight: current ? FontWeight.w700 : FontWeight.w500)),
            ),
          ],
        ),
      ),
    );
  }
}

class _EntryTile extends StatelessWidget {
  final PcEntry entry;
  final bool selecting;
  final bool selected;
  final VoidCallback onTap;
  final VoidCallback onLongPress;
  final VoidCallback onMore;

  const _EntryTile({
    required this.entry,
    required this.selecting,
    required this.selected,
    required this.onTap,
    required this.onLongPress,
    required this.onMore,
  });

  @override
  Widget build(BuildContext context) {
    final (icon, color) = fileIconOf(entry);
    final details = [
      if (entry.folder) 'Folder' else if (entry.size != null) FileTransferService.formatBytes(entry.size!),
      formatModified(entry.modified),
    ].where((s) => s.isNotEmpty).join(' · ');
    return Material(
      color: selected ? WillyColors.cyan.withValues(alpha: 0.10) : Colors.transparent,
      child: InkWell(
        onTap: onTap,
        onLongPress: onLongPress,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 8, 4, 8),
          child: Row(
            children: [
              AnimatedSwitcher(
                duration: const Duration(milliseconds: 180),
                child: selecting
                    ? Icon(
                        selected ? Icons.check_circle_rounded : Icons.radio_button_unchecked_rounded,
                        key: ValueKey(selected),
                        color: selected ? WillyColors.cyan : WillyColors.faint,
                        size: 26,
                      )
                    : Container(
                        key: const ValueKey('icon'),
                        width: 40,
                        height: 40,
                        decoration: BoxDecoration(
                          color: color.withValues(alpha: 0.13),
                          borderRadius: BorderRadius.circular(11),
                        ),
                        child: Icon(icon, color: color, size: 22),
                      ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(entry.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: WillyColors.text, fontSize: 14.5, fontWeight: FontWeight.w500)),
                    if (details.isNotEmpty)
                      Text(details, style: const TextStyle(color: WillyColors.faint, fontSize: 11.5)),
                  ],
                ),
              ),
              if (!selecting)
                IconButton(
                  tooltip: 'More',
                  onPressed: onMore,
                  icon: const Icon(Icons.more_vert_rounded, color: WillyColors.faint, size: 20),
                )
              else
                const SizedBox(width: 12),
            ],
          ),
        ),
      ),
    );
  }
}

class _DriveTile extends StatelessWidget {
  final PcDrive drive;
  final VoidCallback onTap;

  const _DriveTile({required this.drive, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final total = drive.totalGb;
    final free = drive.freeGb;
    final used = (total != null && free != null && total > 0) ? ((total - free) / total).clamp(0.0, 1.0) : null;
    final String label;
    if (drive.isMount) {
      label = drive.label.isEmpty ? drive.name : '${drive.name}  ·  ${drive.label}';
    } else if (drive.label.isEmpty) {
      label = 'Local Disk (${drive.letter})';
    } else {
      label = drive.label.contains(drive.letter) ? drive.label : '${drive.label} (${drive.letter})';
    }
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 8, 16, 8),
        child: Row(
          children: [
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                color: WillyColors.sky.withValues(alpha: 0.13),
                borderRadius: BorderRadius.circular(11),
              ),
              child: const Icon(Icons.storage_rounded, color: WillyColors.sky, size: 22),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(label,
                      style: const TextStyle(color: WillyColors.text, fontSize: 14.5, fontWeight: FontWeight.w500)),
                  if (used != null) ...[
                    const SizedBox(height: 5),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(3),
                      child: LinearProgressIndicator(
                        value: used,
                        minHeight: 5,
                        backgroundColor: WillyColors.border,
                        color: WillyColors.load(used * 100),
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text('${free!.toStringAsFixed(1)} GB free of ${total!.toStringAsFixed(0)} GB',
                        style: const TextStyle(color: WillyColors.faint, fontSize: 11.5)),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Message extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String text;
  final Widget? action;

  const _Message({required this.icon, required this.color, required this.text, this.action});

  @override
  Widget build(BuildContext context) {
    if (text.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.all(28),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(icon, size: 44, color: color),
          const SizedBox(height: 10),
          Text(text, textAlign: TextAlign.center, style: const TextStyle(color: WillyColors.muted, fontSize: 14)),
          if (action != null) ...[const SizedBox(height: 10), action!],
        ],
      ),
    );
  }
}

/// Name dialog for "New folder" and "Rename". Returns a valid Windows name, or null.
Future<String?> askPcName(
  BuildContext context, {
  required String title,
  required String action,
  String initial = '',
  bool selectStem = false,
}) {
  final controller = TextEditingController(text: initial);
  final dot = initial.lastIndexOf('.');
  controller.selection = TextSelection(baseOffset: 0, extentOffset: selectStem && dot > 0 ? dot : initial.length);
  String? error;
  return showDialog<String>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setDialog) {
        void submit() {
          final name = validPcFileName(controller.text);
          if (name == null) {
            setDialog(() => error = r'Use a name without \ / : * ? " < > |');
            return;
          }
          Navigator.pop(ctx, name);
        }

        return AlertDialog(
          title: Text(title, style: const TextStyle(color: WillyColors.text, fontSize: 18)),
          content: TextField(
            controller: controller,
            autofocus: true,
            style: const TextStyle(color: WillyColors.text),
            decoration: InputDecoration(errorText: error),
            onSubmitted: (_) => submit(),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(onPressed: submit, child: Text(action)),
          ],
        );
      },
    ),
  );
}

/// Full-screen folder browser that returns the folder chosen for Move / Copy.
class PcFolderPicker extends StatefulWidget {
  final WillyDevice pc;
  final String startPath;
  final String verb;
  final int count;

  const PcFolderPicker({super.key, required this.pc, required this.startPath, required this.verb, this.count = 1});

  @override
  State<PcFolderPicker> createState() => _PcFolderPickerState();
}

class _PcFolderPickerState extends State<PcFolderPicker> {
  DirListing _dir = const DirListing(path: '');
  List<PcDrive> _drives = const [];
  bool _loading = true;
  String? _error;
  int _serial = 0;

  @override
  void initState() {
    super.initState();
    _load(widget.startPath);
  }

  Future<void> _load(String path) async {
    final serial = ++_serial;
    setState(() {
      _loading = true;
      _error = null;
    });
    final (dir, error) = await listPcDir(widget.pc.id, path);
    if (!mounted || serial != _serial) return;
    setState(() {
      _loading = false;
      if (dir == null) {
        _error = error;
      } else {
        _dir = dir;
        if (dir.drives.isNotEmpty) _drives = dir.drives;
      }
    });
  }

  Future<void> _newFolder() async {
    final name = await askPcName(context, title: 'New folder', action: 'Create', initial: 'New folder');
    if (name == null || !mounted) return;
    final path = joinPcPath(_dir.path, name);
    final res = await ApiService.deviceAction(widget.pc.id, 'manage_file', {'op': 'mkdir', 'path': path});
    if (!mounted) return;
    final (text, ok) = panelResultText(res, ok: 'Created $name.');
    showWillySnack(context, text, error: !ok);
    if (ok) _load(path);
  }

  @override
  Widget build(BuildContext context) {
    final folders = _dir.entries.where((e) => e.folder).toList();
    final crumbs = breadcrumbsFor(_dir.path);
    final what = widget.count == 1 ? '1 item' : '${widget.count} items';
    return PopScope(
      canPop: _dir.isRoot,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _load(_dir.parent ?? '');
      },
      child: Scaffold(
        backgroundColor: WillyColors.bg,
        appBar: AppBar(
          leading: IconButton(icon: const Icon(Icons.close_rounded), onPressed: () => Navigator.of(context).pop()),
          title: Text('${widget.verb} $what to…', style: const TextStyle(fontSize: 17)),
          actions: [
            if (!_dir.isRoot)
              IconButton(
                  tooltip: 'New folder', onPressed: _newFolder, icon: const Icon(Icons.create_new_folder_outlined)),
          ],
          bottom: PreferredSize(
            preferredSize: const Size.fromHeight(40),
            child: SizedBox(
              height: 40,
              child: SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                reverse: true,
                padding: const EdgeInsets.symmetric(horizontal: 8),
                child: Row(
                  children: [
                    _Crumb(
                        label: rootLabel(widget.pc),
                        icon: rootIcon(widget.pc),
                        current: crumbs.isEmpty,
                        onTap: () => _load('')),
                    for (var i = 0; i < crumbs.length; i++) ...[
                      const Icon(Icons.chevron_right_rounded, size: 16, color: WillyColors.faint),
                      _Crumb(
                          label: crumbs[i].label, current: i == crumbs.length - 1, onTap: () => _load(crumbs[i].path)),
                    ],
                  ],
                ),
              ),
            ),
          ),
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? _Message(
                    icon: Icons.error_outline_rounded,
                    color: WillyColors.red,
                    text: _error!,
                    action: TextButton(onPressed: () => _load(_dir.path), child: const Text('Try again')),
                  )
                : ListView(
                    children: [
                      if (_dir.isRoot)
                        for (final d in _drives) _DriveTile(drive: d, onTap: () => _load(d.name)),
                      for (final f in folders)
                        ListTile(
                          leading: const Icon(Icons.folder_rounded, color: WillyColors.amber),
                          title: Text(f.name, style: const TextStyle(color: WillyColors.text)),
                          onTap: () => _load(f.path),
                        ),
                      if (folders.isEmpty && !_dir.isRoot)
                        const Padding(
                          padding: EdgeInsets.all(28),
                          child: Text('No folders here',
                              textAlign: TextAlign.center, style: TextStyle(color: WillyColors.faint)),
                        ),
                    ],
                  ),
        bottomNavigationBar: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
            child: FilledButton.icon(
              style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(50)),
              onPressed: _dir.isRoot || _loading ? null : () => Navigator.of(context).pop(_dir.path),
              icon: Icon(widget.verb == 'Move' ? Icons.drive_file_move_rounded : Icons.file_copy_rounded),
              label: Text(_dir.isRoot ? 'Pick a folder' : '${widget.verb} here'),
            ),
          ),
        ),
      ),
    );
  }
}
