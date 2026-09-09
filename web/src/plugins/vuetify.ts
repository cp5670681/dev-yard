import "vuetify/styles";
import { createVuetify } from "vuetify";
import { aliases, mdi } from "vuetify/iconsets/mdi-svg";

export default createVuetify({
  icons: {
    defaultSet: "mdi",
    aliases,
    sets: { mdi },
  },
  defaults: {
    VBtn: { rounded: "lg" },
    VCard: { rounded: "lg" },
    VChip: { rounded: "lg" },
    VTextField: { variant: "outlined", density: "comfortable" },
    VSelect: { variant: "outlined", density: "comfortable" },
    VTextarea: { variant: "outlined", density: "comfortable" },
    VAlert: { rounded: "lg", variant: "tonal" },
  },
  theme: {
    defaultTheme: "dark",
    themes: {
      dark: {
        dark: true,
        colors: {
          primary: "#E0A04A",
          secondary: "#5AA7FF",
          background: "#0E1218",
          surface: "#1B2330",
          error: "#F07178",
          success: "#3ECF8E",
          info: "#5AA7FF",
          warning: "#E0A04A",
        },
      },
    },
  },
});
