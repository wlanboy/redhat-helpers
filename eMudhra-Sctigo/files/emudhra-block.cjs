'use strict';
// CVE-2026-111943: lehnt TLS-Verbindungen ab, deren Kette eines der acht
// kompromittierten eMudhra-Intermediates enthält. Geprüft wird der
// SHA-256 über den öffentlichen Schlüssel (SPKI), wie bei Go. Das trifft
// die emSign-Ausstellung, den Sectigo-Cross-Sign und alle Umkodierungen.
//
//   NODE_OPTIONS="--require /opt/emudhra/emudhra-block.cjs" node app.js
//
// Greift für alles, was tls.checkServerIdentity nutzt: tls.connect,
// https, fetch (undici). Code mit eigener checkServerIdentity-Option ist
// nicht abgedeckt. Überflüssig ab Node.js 22.23.4, 24.22.1, 26.12.1.
const crypto = require('node:crypto');
const tls = require('node:tls');

// spki_sha256 aus https://emudhra.we-have-a-problem.com/impacted-intermediates.json
const BLOCKED_SPKI = new Map([
  ['EBED6475C2C2D80FCB199F3FE8DD6E6B3CE60B77EB15D4E42C3DEC0C6F5B5105', 'EM DV TLS CA - G3B'],
  ['43BFFA19D6EF651451F6841E019931E96F6F2EAEFF28AB4B6F1CE4576DE2717B', 'EM OV TLS CA - G3B'],
  ['E0254C7FCDC265E2F0F8A428DABB75AB4AE039B4872D62F5EE57F0877D5A8A41', 'EM EV TLS CA - G3B'],
  ['49DD353DC2B919A3770645117D0E60C16115E7D1BC259EEFE734B00EA434FE5F', 'EM DV TLS CA - G3C'],
  ['63ECEFEE431D49523BC1136C4E72D0206A477086CAF606CD5CF025686C109CF8', 'EM OV TLS CA - G3C'],
  ['5C0660A2B164910D8FF589C18E14552E9E0EC237DCEFE2CB736D41A4811F81A0', 'EM EV TLS CA - G3C'],
  ['EC4FFA1AD0AC88081314B7AAFFEF6A91F97CD57291BB5BA686C5415F5EB4F6B6', 'EM OV CS CA - G3B'],
  ['92C063D67E8F14DB71D288E11266FFDAF69CBF115F7BAB14F3BC2F68D25E0E30', 'EM OV CS CA - G3C'],
]);

const original = tls.checkServerIdentity;

tls.checkServerIdentity = function checkServerIdentity(host, cert) {
  // cert ist die verifizierte Kette, issuerCertificate zeigt bei der Root
  // auf sich selbst
  const seen = new Set();
  for (let c = cert; c && !seen.has(c); c = c.issuerCertificate) {
    seen.add(c);
    if (!c.raw) continue;
    // c.pubkey ist bei EC nur der rohe Punkt, daher SPKI aus dem DER holen
    const spki = new crypto.X509Certificate(c.raw).publicKey.export({ type: 'spki', format: 'der' });
    const name = BLOCKED_SPKI.get(crypto.createHash('sha256').update(spki).digest('hex').toUpperCase());
    if (name) {
      const err = new Error(`Kette enthält kompromittiertes eMudhra-Intermediate ${name} (CVE-2026-111943)`);
      err.code = 'ERR_TLS_CERT_EMUDHRA_BLOCKED';
      err.reason = err.message;
      err.host = host;
      err.cert = cert;
      return err;
    }
  }
  return original.call(this, host, cert);
};
