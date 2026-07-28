package com.lancens.api;

public final class IVIEWSAVAPIs {
    public static final int CONNECTION_SUCCESS = 0;
    public static final int TYPE_IVIEWS_DATA = 2;
    public static final int TYPE_P2P_CONNECTION = 3;
    public static final int TYPE_P2P_DATA = 4;

    public interface AVAPISCallback {
        void callback(int type, int cmdOrType, int lenOrStatus, byte[] payload);
    }

    static {
        System.loadLibrary("IVIEWSAVAPIs");
    }

    private IVIEWSAVAPIs() {
    }

    public static native int init(String host);

    public static native int start(String uid, String token, AVAPISCallback callback);

    public static native int send(int session, int cmd, byte[] data, int len);

    public static native int stop(int session);

    public static native int getType(int session);
}
