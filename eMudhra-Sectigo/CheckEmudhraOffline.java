import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.CertPath;
import java.security.cert.CertPathValidator;
import java.security.cert.CertificateFactory;
import java.security.cert.PKIXParameters;
import java.security.cert.TrustAnchor;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Date;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

// Offline-Gegenstück zu CheckEmudhra.java, braucht kein Netz.
//
// Teil 1 prüft die Sperrliste: jedes der 16 Intermediates aus
// testdata/intermediates.pem in allen Kodierungen (48 Stück) gegen seine
// direkte Root aus testdata/roots.pem. Das hängt nur an blocked.certs bzw.
// blacklisted.certs, nicht am cacerts des JDKs.
//
// Teil 2 prüft die 14 Ketten der Testhosts aus testdata/chains gegen den
// Truststore des JDKs (cacerts, jssecacerts oder -Djavax.net.ssl.trustStore).
// Das zeigt, ob dieses JDK den Ketten tatsächlich vertrauen würde.
//
//   /usr/java/jdk-21/bin/java CheckEmudhraOffline.java [testdata]   (JDK 11+)
//   javac -d /tmp CheckEmudhraOffline.java
//   java -cp /tmp CheckEmudhraOffline [testdata]                    (JDK 8)
//
// Exit-Code 0 = alles gesperrt, 1 = mindestens ein Zertifikat nicht
// gesperrt oder eine Kette vertraut, 2 = Fehler in den Testdaten.
public class CheckEmudhraOffline {

    // Ordnung der Kurve P-384, alle ECDSA-Aussteller hier sind P-384
    private static final BigInteger P384_N = new BigInteger(
        "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFC7634D81F4372DDF581A0DB248B0A77AECEC196ACCC52973", 16);
    // OID-Präfix 1.2.840.10045.4 (ecdsa-with-*)
    private static final byte[] ECDSA_OID = {0x2A, (byte) 0x86, 0x48, (byte) 0xCE, 0x3D, 0x04};
    private static final Pattern PEM = Pattern.compile(
        "-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", Pattern.DOTALL);

    private static final CertificateFactory CF;
    static {
        try {
            CF = CertificateFactory.getInstance("X.509");
        } catch (Exception e) {
            throw new ExceptionInInitializerError(e);
        }
    }

    public static void main(String[] args) throws Exception {
        File dir = new File(args.length > 0 ? args[0] : "testdata");
        System.out.println("java.home = " + System.getProperty("java.home"));
        System.out.println("testdata  = " + dir.getAbsolutePath());
        int rc;
        try {
            rc = Math.max(checkBlocklist(dir), checkChains(dir));
        } catch (Exception e) {
            System.out.println("FEHLER " + e);
            rc = 2;
        }
        System.out.println();
        System.out.println(rc == 0 ? "Ergebnis: alles gesperrt"
            : rc == 1 ? "Ergebnis: VERWUNDBAR" : "Ergebnis: Fehler");
        System.exit(rc);
    }

    private static int checkBlocklist(File dir) throws Exception {
        System.out.println();
        System.out.println("== Teil 1: blocked.certs (48 Kodierungen gegen die direkte Root)");
        Set<TrustAnchor> anchors = new HashSet<>();
        for (byte[] der : readPem(new File(dir, "roots.pem"))) {
            anchors.add(new TrustAnchor(parse(der), null));
        }
        PKIXParameters params = new PKIXParameters(anchors);
        params.setRevocationEnabled(false);

        int rc = 0;
        int count = 0;
        for (byte[] der : readPem(new File(dir, "intermediates.pem"))) {
            X509Certificate orig = parse(der);
            String name = cn(orig.getSubjectX500Principal().getName())
                + " / " + cn(orig.getIssuerX500Principal().getName());
            String[] labels = {"DER", "NULL", "s'", "NULL+s'"};
            List<byte[]> variants = variants(der);
            for (int i = 0; i < variants.size(); i++) {
                count++;
                byte[] v = variants.get(i);
                String line = sha256(v) + " " + name + " [" + labels[i] + "]";
                X509Certificate c;
                try {
                    c = parse(v);
                } catch (Exception e) {
                    // Kodierung, die das JDK gar nicht erst annimmt, ist harmlos
                    System.out.println("OK           " + line + ": vom JDK nicht geparst");
                    continue;
                }
                String err = validate(Arrays.asList(c), params);
                if (err == null) {
                    System.out.println("NICHT GESP.  " + line);
                    rc = 1;
                } else if (err.contains("Untrusted certificate")) {
                    System.out.println("OK           " + line);
                } else {
                    System.out.println("FEHLER       " + line + ": " + err);
                    rc = Math.max(rc, 2);
                }
            }
        }
        if (count != 48) {
            System.out.println("FEHLER erwartet 48 Kodierungen, gefunden " + count);
            rc = Math.max(rc, 2);
        }
        return rc;
    }

    private static int checkChains(File dir) throws Exception {
        System.out.println();
        System.out.println("== Teil 2: Ketten der Testhosts gegen den Truststore des JDKs");
        File ts = truststore();
        System.out.println("truststore = " + ts);
        KeyStore ks = KeyStore.getInstance(KeyStore.getDefaultType());
        String pw = System.getProperty("javax.net.ssl.trustStorePassword", "changeit");
        try (InputStream in = new FileInputStream(ts)) {
            ks.load(in, pw.toCharArray());
        }
        File[] files = new File(dir, "chains").listFiles((d, n) -> n.endsWith(".pem"));
        if (files == null || files.length == 0) {
            throw new IllegalStateException("keine Ketten in " + new File(dir, "chains"));
        }
        Arrays.sort(files);

        int rc = 0;
        for (File f : files) {
            List<X509Certificate> chain = new ArrayList<>();
            for (byte[] der : readPem(f)) {
                chain.add(parse(der));
            }
            PKIXParameters params = new PKIXParameters(ks);
            params.setRevocationEnabled(false);
            // Nach Ablauf des Leafs zu einem Zeitpunkt prüfen, an dem es gültig war
            X509Certificate leaf = chain.get(0);
            Date now = new Date();
            if (now.after(leaf.getNotAfter()) || now.before(leaf.getNotBefore())) {
                params.setDate(new Date(leaf.getNotBefore().getTime() + 86_400_000L));
            }
            String host = f.getName().replaceFirst("\\.pem$", "");

            // Mit der vollen Kette und allen Kürzungen versuchen, je nachdem
            // welche Zwischen- oder Root-Zertifikate im Truststore liegen
            boolean trusted = false;
            String blocked = null;
            String other = null;
            for (int k = chain.size(); k >= 1 && !trusted; k--) {
                String err = validate(chain.subList(0, k), params);
                if (err == null) {
                    trusted = true;
                } else if (err.contains("Untrusted certificate")) {
                    blocked = err;
                } else if (other == null) {
                    other = err;
                }
            }
            if (trusted) {
                System.out.println("VERWUNDBAR    " + host);
                rc = 1;
            } else if (blocked != null) {
                System.out.println("OK gesperrt   " + host + ": " + blocked);
            } else {
                System.out.println("OK keine Root " + host + ": " + other);
            }
        }
        return rc;
    }

    private static File truststore() {
        String p = System.getProperty("javax.net.ssl.trustStore");
        if (p != null) {
            return new File(p);
        }
        File sec = new File(System.getProperty("java.home"), "lib" + File.separator + "security");
        File jsse = new File(sec, "jssecacerts");
        return jsse.isFile() ? jsse : new File(sec, "cacerts");
    }

    private static String validate(List<X509Certificate> certs, PKIXParameters params) throws Exception {
        CertPath path = CF.generateCertPath(certs);
        try {
            CertPathValidator.getInstance("PKIX").validate(path, params);
            return null;
        } catch (java.security.cert.CertPathValidatorException e) {
            return e.getMessage();
        }
    }

    // --- PEM / DER ---

    private static List<byte[]> readPem(File f) throws Exception {
        String s = new String(Files.readAllBytes(f.toPath()), StandardCharsets.US_ASCII);
        List<byte[]> out = new ArrayList<>();
        Matcher m = PEM.matcher(s);
        while (m.find()) {
            out.add(Base64.getMimeDecoder().decode(m.group(1)));
        }
        if (out.isEmpty()) {
            throw new IllegalStateException("kein Zertifikat in " + f);
        }
        return out;
    }

    private static X509Certificate parse(byte[] der) throws Exception {
        return (X509Certificate) CF.generateCertificate(new ByteArrayInputStream(der));
    }

    // Kodierungen wie OpenJDKs BlockedCertsConverter: wie ausgestellt, mit
    // umgeschaltetem NULL-Parameter im signatureAlgorithm und bei ECDSA
    // jeweils noch mit s -> n-s.
    private static List<byte[]> variants(byte[] der) {
        List<byte[]> parts = children(der);
        byte[] tbs = parts.get(0);
        byte[] alg = parts.get(1);
        byte[] sig = parts.get(2);
        byte[] alg2 = toggleNull(alg);
        List<byte[]> out = new ArrayList<>();
        out.add(seq(tbs, alg, sig));
        out.add(seq(tbs, alg2, sig));
        if (isEcdsa(alg)) {
            byte[] sig2 = malleate(sig);
            out.add(seq(tbs, alg, sig2));
            out.add(seq(tbs, alg2, sig2));
        }
        return out;
    }

    private static boolean isEcdsa(byte[] alg) {
        byte[] oid = children(alg).get(0);
        int[] h = header(oid, 0);
        return h[1] >= ECDSA_OID.length
            && Arrays.equals(Arrays.copyOfRange(oid, h[0], h[0] + ECDSA_OID.length), ECDSA_OID);
    }

    private static byte[] toggleNull(byte[] alg) {
        List<byte[]> c = children(alg);
        if (c.size() == 1) {
            return tlv(0x30, concat(c.get(0), new byte[] {0x05, 0x00}));
        }
        return tlv(0x30, c.get(0));
    }

    private static byte[] malleate(byte[] bitString) {
        int[] h = header(bitString, 0);
        // erstes Inhaltsbyte = Anzahl ungenutzter Bits (0)
        byte[] sigSeq = Arrays.copyOfRange(bitString, h[0] + 1, h[0] + h[1]);
        List<byte[]> rs = children(sigSeq);
        int[] hs = header(rs.get(1), 0);
        BigInteger s = new BigInteger(1, Arrays.copyOfRange(rs.get(1), hs[0], hs[0] + hs[1]));
        byte[] s2 = tlv(0x02, P384_N.subtract(s).toByteArray());
        return tlv(0x03, concat(new byte[] {0x00}, tlv(0x30, concat(rs.get(0), s2))));
    }

    private static byte[] seq(byte[]... parts) {
        return tlv(0x30, concat(parts));
    }

    // {Inhaltsbeginn, Länge}
    private static int[] header(byte[] b, int i) {
        int len = b[i + 1] & 0xff;
        int pos = i + 2;
        if (len >= 0x80) {
            int n = len & 0x7f;
            len = 0;
            for (int k = 0; k < n; k++) {
                len = (len << 8) | (b[pos + k] & 0xff);
            }
            pos += n;
        }
        return new int[] {pos, len};
    }

    private static List<byte[]> children(byte[] seq) {
        int[] h = header(seq, 0);
        List<byte[]> out = new ArrayList<>();
        int i = h[0];
        while (i < h[0] + h[1]) {
            int[] c = header(seq, i);
            out.add(Arrays.copyOfRange(seq, i, c[0] + c[1]));
            i = c[0] + c[1];
        }
        return out;
    }

    private static byte[] tlv(int tag, byte[] content) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(tag);
        int n = content.length;
        if (n < 0x80) {
            out.write(n);
        } else {
            byte[] l = BigInteger.valueOf(n).toByteArray();
            int off = l[0] == 0 ? 1 : 0;
            out.write(0x80 | (l.length - off));
            out.write(l, off, l.length - off);
        }
        out.write(content, 0, n);
        return out.toByteArray();
    }

    private static byte[] concat(byte[]... parts) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (byte[] p : parts) {
            out.write(p, 0, p.length);
        }
        return out.toByteArray();
    }

    private static String sha256(byte[] der) throws Exception {
        StringBuilder sb = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(der)) {
            sb.append(String.format("%02X", b));
        }
        return sb.toString();
    }

    private static String cn(String dn) {
        Matcher m = Pattern.compile("CN=([^,]+)").matcher(dn);
        return m.find() ? m.group(1) : dn;
    }
}
