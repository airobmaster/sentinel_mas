import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "./styles.css";

import { createTheme, MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { AuthProvider } from "./auth";

const theme = createTheme({
  fontFamily: "Inter, 'Segoe UI', system-ui, sans-serif",
  headings: { fontFamily: "Inter, 'Segoe UI', system-ui, sans-serif", fontWeight: "700" },
  // Red palette with pastel tints (colours only: no third-party logos or names)
  primaryColor: "brand",
  primaryShade: 6,
  colors: {
    brand: ["#FFF1F1", "#FDE2E2", "#F9C4C4", "#F49D9D", "#EE6E6E", "#E93B3B", "#D91E18", "#B71C1C", "#951616", "#741111"],
    navy: ["#E8EEF6", "#C9D7EA", "#A6BEDD", "#7FA1CD", "#5A84BC", "#3D6BA9", "#2C5690", "#1F4473", "#143257", "#0B2545"],
  },
  defaultRadius: "md",
});

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme}>
      <Notifications position="top-right" />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
