import java.net.URI;
import javax.net.ssl.SSLHandshakeException;

// Baut TLS-Verbindungen zu den Testhosts von emudhra.we-have-a-problem.com
// auf. Jeder Host liefert eine Kette über ein kompromittiertes eMudhra-
// Intermediate. Ein erfolgreicher Handshake heißt: dieses JDK vertraut
// den Intermediates noch.
//
//   /usr/java/jdk-21/bin/java CheckEmudhra.java           (JDK 11+)
//   javac CheckEmudhra.java && java CheckEmudhra          (JDK 8)
//
// Exit-Code 0 = alle abgelehnt, 1 = mindestens ein Host vertraut,
// 2 = mindestens ein Host nicht erreichbar.
public class CheckEmudhra {

    private static final String[] HOSTS = {
        "dv-g3b-emudhra.we-have-a-problem.com",
        "dv-g3b-aaa-emudhra.we-have-a-problem.com",
        "dv-g3c-emudhra.we-have-a-problem.com",
        "dv-g3c-aaa-emudhra.we-have-a-problem.com",
        "ov-g3b-emudhra.we-have-a-problem.com",
        "ov-g3b-aaa-emudhra.we-have-a-problem.com",
        "ov-g3c-emudhra.we-have-a-problem.com",
        "ov-g3c-aaa-emudhra.we-have-a-problem.com",
        "ev-g3b-emudhra.we-have-a-problem.com",
        "ev-g3b-aaa-emudhra.we-have-a-problem.com",
        "ev-g3c-emudhra.we-have-a-problem.com",
        "ev-g3c-aaa-emudhra.we-have-a-problem.com",
        "dv-g3b-noct-emudhra.we-have-a-problem.com",
        "dv-g3b-aaa-noct-emudhra.we-have-a-problem.com",
    };

    public static void main(String[] args) {
        String[] hosts = args.length > 0 ? args : HOSTS;
        System.out.println("java.home = " + System.getProperty("java.home"));
        int rc = 0;
        for (String h : hosts) {
            try {
                URI.create("https://" + h + "/").toURL().openStream().close();
                System.out.println("VERWUNDBAR " + h);
                rc = Math.max(rc, 1);
            } catch (SSLHandshakeException e) {
                System.out.println("OK         " + h + ": " + e.getMessage());
            } catch (Exception e) {
                System.out.println("FEHLER     " + h + ": " + e);
                rc = Math.max(rc, 2);
            }
        }
        System.exit(rc);
    }
}
