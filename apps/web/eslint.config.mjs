import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // eslint-plugin-react-hooks v7 (bundled with eslint-config-next 16) added
      // React-Compiler-oriented rules. The dashboard's fetch-in-useEffect
      // data loading and theme hydration trip this one. It is guidance, not a
      // correctness/security issue, and refactoring every page is out of
      // scope for the security upgrade. Revisit when adopting the compiler.
      "react-hooks/set-state-in-effect": "off",
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);

export default eslintConfig;
