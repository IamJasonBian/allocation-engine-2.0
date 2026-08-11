/**
 * Allocation Dashboard — TestFlight shell.
 *
 * A WebView over the deployed Netlify dashboard: one codebase, the app always
 * shows what the web shows. The URL comes from app.json `extra.dashboardUrl`.
 * Auth: the repo's request-token scheme (auth-service/gen_token.py). Set
 * `extra.dashboardToken` and the shell opens the page with ?token=… once;
 * the page moves it into localStorage and sends it as a Bearer header to
 * the render-logs function. Portfolio reads stay public Trading DB calls.
 */

import Constants from "expo-constants";
import { StatusBar } from "expo-status-bar";
import React, { useRef, useState } from "react";
import { ActivityIndicator, Pressable, SafeAreaView, StyleSheet, Text, View } from "react-native";
import { WebView } from "react-native-webview";

const BASE_URL: string =
  Constants.expoConfig?.extra?.dashboardUrl ??
  "https://allocation-engine-dashboard.netlify.app";
const TOKEN: string = Constants.expoConfig?.extra?.dashboardToken ?? "";
const DASHBOARD_URL = TOKEN ? `${BASE_URL}/?token=${encodeURIComponent(TOKEN)}` : BASE_URL;

export default function App() {
  const webref = useRef<WebView>(null);
  const [failed, setFailed] = useState<string | null>(null);

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="light" backgroundColor="#0000f2" />
      {failed ? (
        <View style={styles.err}>
          <Text style={styles.errTitle}>DASHBOARD UNREACHABLE</Text>
          <Text style={styles.errBody}>{failed}</Text>
          <Pressable
            style={styles.retry}
            onPress={() => {
              setFailed(null);
              webref.current?.reload();
            }}
          >
            <Text style={styles.retryText}>RETRY</Text>
          </Pressable>
        </View>
      ) : (
        <WebView
          ref={webref}
          source={{ uri: DASHBOARD_URL }}
          style={styles.web}
          startInLoadingState
          renderLoading={() => (
            <View style={styles.loading}>
              <ActivityIndicator color="#edff45" size="large" />
            </View>
          )}
          onError={(e) => setFailed(e.nativeEvent.description || "load error")}
          pullToRefreshEnabled
          allowsBackForwardNavigationGestures
          setSupportMultipleWindows={false}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0000f2" },
  web: { flex: 1, backgroundColor: "#0000f2" },
  loading: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "#0000f2",
    alignItems: "center",
    justifyContent: "center",
  },
  err: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12, padding: 24 },
  errTitle: { color: "#f5f5f5", fontFamily: "Courier", letterSpacing: 2, fontSize: 13 },
  errBody: { color: "#f5f5f599", fontFamily: "Courier", fontSize: 12, textAlign: "center" },
  retry: { borderWidth: 1, borderColor: "#edff45", paddingVertical: 8, paddingHorizontal: 22 },
  retryText: { color: "#edff45", fontFamily: "Courier", letterSpacing: 2, fontSize: 12 },
});
