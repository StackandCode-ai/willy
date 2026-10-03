import 'package:flutter/material.dart';

import '../models/phone_skills.dart';
import '../services/telemetry_service.dart';
import '../theme.dart';
import 'common.dart';

enum _Access { runtime, notificationListener, overlay }

class _Skill {
  final String key;
  final String title;
  final String subtitle;
  final IconData icon;
  final Color color;
  final _Access access;

  const _Skill(this.key, this.title, this.subtitle, this.icon, this.color, this.access);
}

const _skills = [
  _Skill(PhonePermissions.callPhone, 'Calls', 'Place calls straight away', Icons.call_rounded, WillyColors.green,
      _Access.runtime),
  _Skill(PhonePermissions.sendSms, 'SMS', 'Send texts without opening Messages', Icons.sms_rounded, WillyColors.sky,
      _Access.runtime),
  _Skill(PhonePermissions.readContacts, 'Contacts', 'Call, text and WhatsApp people by name',
      Icons.contacts_rounded, WillyColors.purple, _Access.runtime),
  _Skill(PhonePermissions.notificationAccess, 'Notification access', 'Read your recent notifications aloud',
      Icons.mark_email_unread_rounded, WillyColors.amber, _Access.notificationListener),
  _Skill(PhonePermissions.overlay, 'Background actions',
      'Open apps, WhatsApp, alarms and maps while Willy is in the background ("Display over other apps")',
      Icons.open_in_new_rounded, WillyColors.cyan, _Access.overlay),
  _Skill(PhonePermissions.postNotifications, 'Notifications', 'Alerts, reminders and tap-to-finish prompts',
      Icons.notifications_rounded, WillyColors.pink, _Access.runtime),
];

/// Settings section: which phone skills Willy may use, with a shortcut to grant each one.
/// Re-reads the permissions when shown and whenever the app comes back to the front
/// (e.g. after the user flipped a switch in Android's settings).
class PhoneSkillsPanel extends StatefulWidget {
  const PhoneSkillsPanel({super.key});

  @override
  State<PhoneSkillsPanel> createState() => _PhoneSkillsPanelState();
}

class _PhoneSkillsPanelState extends State<PhoneSkillsPanel> with WidgetsBindingObserver {
  PhonePermissions? _perms;
  String? _busyKey;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refresh();
  }

  Future<void> _refresh() async {
    final perms = await MobileTelemetryService.getPhonePermissions();
    if (mounted) setState(() => _perms = perms);
  }

  Future<void> _grant(_Skill skill) async {
    final perms = _perms;
    if (perms == null || _busyKey != null) return;
    setState(() => _busyKey = skill.key);
    PhonePermissions? updated;
    switch (skill.access) {
      case _Access.notificationListener:
        await MobileTelemetryService.openNotificationAccessSettings();
      case _Access.overlay:
        await MobileTelemetryService.openOverlaySettings();
      case _Access.runtime:
        if (perms.isBlocked(skill.key)) {
          await MobileTelemetryService.openAppSettings();
        } else {
          updated = await MobileTelemetryService.requestPhonePermissions([skill.key]);
        }
    }
    if (!mounted) return;
    setState(() {
      _busyKey = null;
      if (updated != null && updated.available) _perms = updated;
    });
  }

  @override
  Widget build(BuildContext context) {
    final perms = _perms;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SectionTitle(
          'Phone skills',
          icon: Icons.phone_android_rounded,
          trailing: perms != null && perms.available
              ? StatusPill(
                  text: '${perms.onCount}/${_skills.length} ON',
                  color: perms.onCount == _skills.length ? WillyColors.green : WillyColors.amber,
                  dot: false,
                )
              : null,
        ),
        const Text(
          'Let Willy call, text, open apps and more on this phone when you ask by voice or from your PC.',
          style: TextStyle(color: WillyColors.muted, fontSize: 12, height: 1.35),
        ),
        const SizedBox(height: 10),
        if (perms == null)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 14),
            child: Center(
              child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
            ),
          )
        else if (!perms.available)
          const Text('Phone skills need the Willy Android app.',
              style: TextStyle(color: WillyColors.faint, fontSize: 12))
        else
          Container(
            decoration: BoxDecoration(
              color: WillyColors.bgElevated,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: WillyColors.border),
            ),
            child: Column(
              children: [
                for (var i = 0; i < _skills.length; i++) ...[
                  if (i > 0) const Divider(height: 1, thickness: 1, color: WillyColors.border),
                  _SkillRow(
                    skill: _skills[i],
                    on: perms.isOn(_skills[i].key),
                    blocked: perms.isBlocked(_skills[i].key),
                    busy: _busyKey == _skills[i].key,
                    onGrant: () => _grant(_skills[i]),
                  ),
                ],
              ],
            ),
          ),
        if (perms != null && perms.available && !perms.hasTelephony)
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Text("This device has no phone service, so calls and texts won't work.",
                style: TextStyle(color: WillyColors.amber, fontSize: 11)),
          ),
        const SizedBox(height: 14),
        const Text('Try saying',
            style: TextStyle(color: WillyColors.muted, fontSize: 12, fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [for (final example in phoneSkillExamples) _ExampleChip(example)],
        ),
      ],
    );
  }
}

class _SkillRow extends StatelessWidget {
  final _Skill skill;
  final bool on;
  final bool blocked;
  final bool busy;
  final VoidCallback onGrant;

  const _SkillRow({
    required this.skill,
    required this.on,
    required this.blocked,
    required this.busy,
    required this.onGrant,
  });

  @override
  Widget build(BuildContext context) {
    final opensSettings = blocked || skill.access != _Access.runtime;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(7),
            decoration: BoxDecoration(
              color: skill.color.withValues(alpha: 0.14),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Icon(skill.icon, size: 18, color: skill.color),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(skill.title,
                    style: const TextStyle(color: WillyColors.text, fontSize: 14, fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text(skill.subtitle, style: const TextStyle(color: WillyColors.faint, fontSize: 11, height: 1.3)),
              ],
            ),
          ),
          const SizedBox(width: 8),
          if (on)
            const StatusPill(text: 'ON', color: WillyColors.green)
          else if (busy)
            const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
          else
            OutlinedButton(
              onPressed: onGrant,
              style: OutlinedButton.styleFrom(
                visualDensity: VisualDensity.compact,
                padding: const EdgeInsets.symmetric(horizontal: 12),
                side: BorderSide(color: WillyColors.cyan.withValues(alpha: 0.5)),
                textStyle: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
              ),
              child: Text(opensSettings ? 'Settings' : 'Allow'),
            ),
        ],
      ),
    );
  }
}

class _ExampleChip extends StatelessWidget {
  final String text;

  const _ExampleChip(this.text);

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: WillyColors.cyan.withValues(alpha: 0.07),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: WillyColors.cyan.withValues(alpha: 0.25)),
      ),
      child: Text('“$text”', style: const TextStyle(color: WillyColors.textSoft, fontSize: 12)),
    );
  }
}
