import 'package:flutter_test/flutter_test.dart';
import 'package:willy_mobile/services/file_transfer_service.dart';

void main() {
  group('hubFileUrl', () {
    test('relative links resolve against the hub, keeping a path prefix', () {
      expect(FileTransferService.hubFileUrl('/api/v1/files/abc123', 'http://192.168.1.5:8000'),
          'http://192.168.1.5:8000/api/v1/files/abc123');
      expect(FileTransferService.hubFileUrl('/api/v1/files/x', 'https://example.com/willy/'),
          'https://example.com/willy/api/v1/files/x');
      expect(FileTransferService.hubFileUrl('api/v1/files/x', 'https://example.com'), 'https://example.com/api/v1/files/x');
    });

    test('absolute http(s) links are kept, other schemes refused', () {
      expect(FileTransferService.hubFileUrl('https://cdn.example.com/f', 'http://hub'), 'https://cdn.example.com/f');
      expect(FileTransferService.hubFileUrl('file:///sdcard/x', 'http://hub'), isNull);
      expect(FileTransferService.hubFileUrl('  ', 'http://hub'), isNull);
    });

    test('the token only goes to the hub origin', () {
      expect(FileTransferService.sameOrigin('http://10.0.0.2:8000/api/v1/files/a', 'http://10.0.0.2:8000'), isTrue);
      expect(FileTransferService.sameOrigin('http://evil.example/api', 'http://10.0.0.2:8000'), isFalse);
      expect(FileTransferService.sameOrigin('https://10.0.0.2:8000/a', 'http://10.0.0.2:8000'), isFalse);
    });
  });

  group('parseUploadResponse', () {
    test('success passes the hub reply through', () {
      final r = FileTransferService.parseUploadResponse(
          200, '{"success":true,"file":{"id":"f1"},"delivered":true,"reply":"Sent photo.jpg to HariG."}');
      expect(r['success'], isTrue);
      expect(r['delivered'], isTrue);
      expect(r['reply'], 'Sent photo.jpg to HariG.');
      expect(r['file'], {'id': 'f1'});
    });

    test('a reply is made up when the hub gives none', () {
      final r = FileTransferService.parseUploadResponse(200, '{"success":true,"delivered":false}', name: 'a.pdf');
      expect(r['success'], isTrue);
      expect(r['reply'], contains('a.pdf'));
    });

    test('errors are readable', () {
      expect(FileTransferService.parseUploadResponse(401, '')['error'], contains('token'));
      expect(FileTransferService.parseUploadResponse(413, '', name: 'big.iso')['error'], contains('big.iso'));
      expect(FileTransferService.parseUploadResponse(500, '{"detail":"disk full"}')['error'], 'disk full');
      expect(FileTransferService.parseUploadResponse(-1, '', error: 'offline')['error'], 'offline');
      final r = FileTransferService.parseUploadResponse(200, '{"success":false,"reply":"No PC online."}');
      expect(r['success'], isFalse);
      expect(r['error'], 'No PC online.');
    });
  });

  test('PhoneFile.fromMap tolerates missing fields', () {
    expect(PhoneFile.fromMap({'name': 'x'}), isNull);
    final f = PhoneFile.fromMap({'uri': 'content://a/1', 'size': 12.0})!;
    expect(f.name, 'file');
    expect(f.size, 12);
    expect(f.mime, 'application/octet-stream');
  });

  test('formatBytes', () {
    expect(FileTransferService.formatBytes(512), '512 B');
    expect(FileTransferService.formatBytes(100 * 1024 * 1024), '100.0 MB');
  });
}
