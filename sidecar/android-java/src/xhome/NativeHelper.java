package xhome;

import com.lancens.api.IVIEWSAVAPIs;
import java.io.BufferedReader;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class NativeHelper {
    private static final byte[] MAGIC = new byte[] {'X', 'H', 'F', '1'};
    private static final byte[] EMPTY_BYTES = new byte[0];

    private static int session = 0;

    private NativeHelper() {
    }

    public static void main(String[] args) throws Exception {
        BufferedReader reader = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        DataOutputStream callbackOut = new DataOutputStream(System.out);
        String firstLine = reader.readLine();
        if (firstLine == null) {
            throw new IllegalArgumentException("Missing session JSON on stdin");
        }

        String uid = jsonString(firstLine, "uid", true);
        String token = jsonString(firstLine, "token", true);
        String host = jsonString(firstLine, "native_iot_host", true);

        int initResult = IVIEWSAVAPIs.init(host);
        log("init(" + host + ") -> " + initResult);

        session = IVIEWSAVAPIs.start(uid, token, (type, cmdOrType, lenOrStatus, payload) -> {
            try {
                writeCallback(callbackOut, type, cmdOrType, lenOrStatus, payload == null ? EMPTY_BYTES : payload);
            } catch (IOException exc) {
                log("callback write failed: " + exc);
            }
        });
        log("start(" + uid + ", token) -> session " + session);

        String line;
        while ((line = reader.readLine()) != null) {
            String action = jsonString(line, "action", false);
            if ("send".equals(action)) {
                int cmd = jsonInt(line, "cmd", 0);
                byte[] data = jsonBase64(line, "data_base64");
                int result = IVIEWSAVAPIs.send(session, cmd, data, data.length);
                log("send(" + cmd + ", " + data.length + " bytes) -> " + result);
            } else if ("stop".equals(action)) {
                int cmd = jsonInt(line, "cmd", 21);
                IVIEWSAVAPIs.send(session, cmd, EMPTY_BYTES, 0);
                IVIEWSAVAPIs.stop(session);
                log("stop(" + cmd + ")");
                return;
            }
        }
    }

    private static synchronized void writeCallback(
        DataOutputStream out,
        int type,
        int cmdOrType,
        int lenOrStatus,
        byte[] payload
    ) throws IOException {
        ByteBuffer header = ByteBuffer.allocate(20).order(ByteOrder.LITTLE_ENDIAN);
        header.put(MAGIC);
        header.putInt(type);
        header.putInt(cmdOrType);
        header.putInt(lenOrStatus);
        header.putInt(payload.length);
        out.write(header.array());
        out.write(payload);
        out.flush();
    }

    private static String jsonString(String json, String key, boolean required) {
        Pattern pattern = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"((?:\\\\.|[^\"])*)\"");
        Matcher matcher = pattern.matcher(json);
        if (!matcher.find()) {
            if (required) {
                throw new IllegalArgumentException("Missing JSON string field: " + key);
            }
            return null;
        }
        return unescapeJsonString(matcher.group(1));
    }

    private static int jsonInt(String json, String key, int defaultValue) {
        Pattern pattern = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*(-?\\d+)");
        Matcher matcher = pattern.matcher(json);
        if (!matcher.find()) {
            return defaultValue;
        }
        return Integer.parseInt(matcher.group(1));
    }

    private static byte[] jsonBase64(String json, String key) {
        String value = jsonString(json, key, false);
        if (value == null || value.isEmpty()) {
            return EMPTY_BYTES;
        }
        return Base64.getDecoder().decode(value);
    }

    private static String unescapeJsonString(String value) {
        return value
            .replace("\\\"", "\"")
            .replace("\\\\", "\\")
            .replace("\\/", "/")
            .replace("\\b", "\b")
            .replace("\\f", "\f")
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t");
    }

    private static void log(String message) {
        System.err.println("[xhome-native-helper] " + message);
        System.err.flush();
    }
}
