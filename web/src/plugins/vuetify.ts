import "vuetify/styles";
import { createVuetify } from "vuetify";
import { aliases, mdi } from "vuetify/iconsets/mdi-svg";

const savedTheme =
  typeof localStorage !== "undefined"
    ? localStorage.getItem("dev-yard-theme")
    : null;

export default createVuetify({
  icons: {
    defaultSet: "mdi",
    aliases,
    sets: { mdi },
  },
  defaults: {
    VBtn: { rounded: "sm", elevation: 0 },
    VCard: { rounded: "sm", elevation: 0 },
    VChip: { rounded: "sm" },
    VTextField: { variant: "outlined", density: "compact" },
    VSelect: { variant: "outlined", density: "compact" },
    VTextarea: { variant: "outlined", density: "compact" },
    VAlert: { rounded: "sm", variant: "tonal" },
  },
  theme: {
    defaultTheme: savedTheme === "dark" ? "dark" : "light",
    themes: {
      light: {
        dark: false,
        colors: {
          // Jira Server / Data Center RapidBoard Palette (Atlassian Design System)
          primary: "#0052CC", // Classic Jira Blue (B400)
          "primary-darken-1": "#0747A6",
          secondary: "#6554C0", // Jira Purple (Epic / Discovery P400)
          "secondary-darken-1": "#5243AA",
          background: "#F4F5F7", // Jira RapidBoard Canvas Light Grey (N10)
          surface: "#FFFFFF", // Jira White Cards / Panels / Modals
          "surface-variant": "#EBECF0", // Jira Column & Header Surface (N30)
          "on-background": "#172B4D", // Jira Deep Slate Navy Text (N800)
          "on-surface": "#172B4D",
          "on-surface-variant": "#5E6C84", // Jira Muted Slate Text (N500)
          error: "#DE350B", // Jira Bug / Blocked Red (R400)
          success: "#00875A", // Jira Done Green (G400)
          info: "#0052CC", // Jira Info Blue
          warning: "#FF8B00", // Jira Warning Orange (Y400)
        },
      },
      dark: {
        dark: true,
        colors: {
          // Jira Dark Palette
          primary: "#579DFF", // Jira Dark Primary Blue (B400 Dark)
          "primary-darken-1": "#0C66E4",
          secondary: "#998DD9", // Jira Dark Purple (Epic / Discovery)
          "secondary-darken-1": "#6554C0",
          background: "#1D2125", // Jira Dark Canvas background
          surface: "#22272B", // Jira Dark Surface (Cards, Drawers, Modals)
          "surface-variant": "#282E33", // Jira Dark Elevated header & column surface
          "on-background": "#DEE4EA",
          "on-surface": "#DEE4EA",
          "on-surface-variant": "#B6C2CF",
          error: "#F87168", // Jira Dark Bug / Blocked Red (R400 Dark)
          success: "#4BCE97", // Jira Dark Done Green (G400 Dark)
          info: "#579DFF", // Jira Dark Info Blue
          warning: "#F5CD47", // Jira Dark Warning Amber (Y400 Dark)
        },
      },
    },
  },
});


