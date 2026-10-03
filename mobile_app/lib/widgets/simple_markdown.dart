import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme.dart';

/// The bits of markdown Willy's replies use: paragraphs, headings, bullet and numbered
/// lists, fenced code blocks, and inline **bold**, *italic*, `code` and [links](url).
enum MdBlockType { paragraph, heading, bullet, numbered, code }

class MdBlock {
  final MdBlockType type;
  final String text;
  final int level; // heading level, or list indent
  final String? marker; // "3." for numbered items

  const MdBlock(this.type, this.text, {this.level = 0, this.marker});

  @override
  String toString() => 'MdBlock($type, "$text", level: $level, marker: $marker)';
}

class MdSpan {
  final String text;
  final bool bold;
  final bool italic;
  final bool code;
  final String? link;

  const MdSpan(this.text, {this.bold = false, this.italic = false, this.code = false, this.link});

  @override
  String toString() => 'MdSpan("$text"${bold ? ' b' : ''}${italic ? ' i' : ''}${code ? ' c' : ''})';
}

final _fence = RegExp(r'^\s*```');
final _heading = RegExp(r'^\s{0,3}(#{1,6})\s+(.*)$');
final _bullet = RegExp(r'^(\s*)[-*+•]\s+(.*)$');
final _numbered = RegExp(r'^(\s*)(\d{1,3})[.)]\s+(.*)$');

List<MdBlock> parseMarkdownBlocks(String source) {
  final blocks = <MdBlock>[];
  final para = <String>[];
  void flush() {
    if (para.isNotEmpty) {
      blocks.add(MdBlock(MdBlockType.paragraph, para.join('\n')));
      para.clear();
    }
  }

  final lines = source.replaceAll('\r\n', '\n').split('\n');
  for (var i = 0; i < lines.length; i++) {
    final line = lines[i];
    if (_fence.hasMatch(line)) {
      flush();
      final code = <String>[];
      i++;
      while (i < lines.length && !_fence.hasMatch(lines[i])) {
        code.add(lines[i]);
        i++;
      }
      blocks.add(MdBlock(MdBlockType.code, code.join('\n')));
      continue;
    }
    if (line.trim().isEmpty) {
      flush();
      continue;
    }
    final h = _heading.firstMatch(line);
    if (h != null) {
      flush();
      blocks.add(MdBlock(MdBlockType.heading, h.group(2)!.trim(), level: h.group(1)!.length));
      continue;
    }
    final b = _bullet.firstMatch(line);
    if (b != null) {
      flush();
      blocks.add(MdBlock(MdBlockType.bullet, b.group(2)!.trim(), level: (b.group(1)!.length ~/ 2).clamp(0, 3)));
      continue;
    }
    final n = _numbered.firstMatch(line);
    if (n != null) {
      flush();
      blocks.add(MdBlock(MdBlockType.numbered, n.group(3)!.trim(),
          level: (n.group(1)!.length ~/ 2).clamp(0, 3), marker: '${n.group(2)}.'));
      continue;
    }
    para.add(line.trimRight());
  }
  flush();
  return blocks;
}

final _inline = RegExp(
  r'(`[^`\n]+`)'
  r'|(\*\*[^*\n]+?\*\*)'
  r'|(__[^_\n]+?__)'
  r'|(\[[^\]\n]+\]\([^)\s]+\))'
  r'|((?<![\w*])\*[^*\s](?:[^*\n]*?[^*\s])?\*(?![\w*]))'
  r'|((?<![\w_])_[^_\s](?:[^_\n]*?[^_\s])?_(?![\w_]))',
);

List<MdSpan> parseInlineMarkdown(String text) {
  final spans = <MdSpan>[];
  var last = 0;
  for (final m in _inline.allMatches(text)) {
    if (m.start > last) spans.add(MdSpan(text.substring(last, m.start)));
    final t = m.group(0)!;
    if (m.group(1) != null) {
      spans.add(MdSpan(t.substring(1, t.length - 1), code: true));
    } else if (m.group(2) != null || m.group(3) != null) {
      spans.add(MdSpan(t.substring(2, t.length - 2), bold: true));
    } else if (m.group(4) != null) {
      final close = t.indexOf('](');
      spans.add(MdSpan(t.substring(1, close), link: t.substring(close + 2, t.length - 1)));
    } else {
      spans.add(MdSpan(t.substring(1, t.length - 1), italic: true));
    }
    last = m.end;
  }
  if (last < text.length) spans.add(MdSpan(text.substring(last)));
  return spans;
}

/// Plain text of a markdown reply (for one-line previews and captions).
String markdownToPlain(String source) {
  final out = <String>[];
  for (final b in parseMarkdownBlocks(source)) {
    final text = parseInlineMarkdown(b.text).map((s) => s.text).join();
    switch (b.type) {
      case MdBlockType.bullet:
        out.add('• $text');
        break;
      case MdBlockType.numbered:
        out.add('${b.marker} $text');
        break;
      case MdBlockType.code:
        out.add(b.text);
        break;
      default:
        out.add(text);
    }
  }
  return out.join('\n');
}

/// Renders a reply with [parseMarkdownBlocks] / [parseInlineMarkdown]. Long-press copies it.
class SimpleMarkdown extends StatelessWidget {
  final String text;
  final TextStyle? style;

  const SimpleMarkdown(this.text, {super.key, this.style});

  static const _mono = TextStyle(fontFamily: 'monospace', fontSize: 12.5, color: WillyColors.cyan, height: 1.35);

  List<InlineSpan> _inlineSpans(String text, TextStyle base) {
    return parseInlineMarkdown(text).map((s) {
      if (s.code) {
        return TextSpan(
          text: s.text,
          style: base.merge(_mono).copyWith(backgroundColor: WillyColors.border.withValues(alpha: 0.7)),
        );
      }
      if (s.link != null) {
        return TextSpan(
          text: s.text,
          style: base.copyWith(color: WillyColors.sky, decoration: TextDecoration.underline),
        );
      }
      return TextSpan(
        text: s.text,
        style: base.copyWith(
          fontWeight: s.bold ? FontWeight.w700 : null,
          fontStyle: s.italic ? FontStyle.italic : null,
          color: s.bold ? WillyColors.text : null,
        ),
      );
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    final base = const TextStyle(color: WillyColors.textSoft, fontSize: 14, height: 1.4).merge(style);
    final blocks = parseMarkdownBlocks(text);
    final children = <Widget>[];
    for (var i = 0; i < blocks.length; i++) {
      final b = blocks[i];
      final gap = i == 0 ? 0.0 : (b.type == MdBlockType.bullet || b.type == MdBlockType.numbered ? 3.0 : 8.0);
      Widget child;
      switch (b.type) {
        case MdBlockType.heading:
          final size = (base.fontSize ?? 14) + (b.level <= 1 ? 3 : (b.level == 2 ? 2 : 1));
          child = Text.rich(
            TextSpan(
                children: _inlineSpans(
                    b.text, base.copyWith(fontSize: size, fontWeight: FontWeight.w700, color: WillyColors.text))),
          );
          break;
        case MdBlockType.bullet:
        case MdBlockType.numbered:
          child = Padding(
            padding: EdgeInsets.only(left: 4.0 + b.level * 14),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: b.type == MdBlockType.bullet ? 14 : 22,
                  child: Text(
                    b.type == MdBlockType.bullet ? '•' : b.marker!,
                    style: base.copyWith(color: WillyColors.cyan, fontWeight: FontWeight.w700),
                  ),
                ),
                Expanded(child: Text.rich(TextSpan(children: _inlineSpans(b.text, base)))),
              ],
            ),
          );
          break;
        case MdBlockType.code:
          child = Container(
            width: double.infinity,
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: WillyColors.bg,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: WillyColors.border),
            ),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Text(b.text, style: _mono),
            ),
          );
          break;
        case MdBlockType.paragraph:
          child = Text.rich(TextSpan(children: _inlineSpans(b.text, base)));
          break;
      }
      children.add(Padding(padding: EdgeInsets.only(top: gap), child: child));
    }
    return GestureDetector(
      onLongPress: () {
        Clipboard.setData(ClipboardData(text: markdownToPlain(text)));
        HapticFeedback.selectionClick();
        final messenger = ScaffoldMessenger.maybeOf(context);
        messenger?.hideCurrentSnackBar();
        messenger?.showSnackBar(const SnackBar(content: Text('Copied'), duration: Duration(seconds: 1)));
      },
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
    );
  }
}
