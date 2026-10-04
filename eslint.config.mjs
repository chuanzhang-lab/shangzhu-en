// ESLint 扁平配置（E-04 门禁）——只对前端静态资源生效，不参与运行时。
// 错误级只开两条「点状事故 → 面状规则」：
//   no-shadow（含 builtinGlobals）：杜绝任务对象参数 t 遮蔽翻译函数 / renameInput
//     遮蔽全局 input 这类事故复发。builtinGlobals 是关键——浏览器各脚本共享全局
//     运行时，新文件里的局部 t 同样遮蔽 app.js 的全局翻译函数，按文件隔离的
//     上层作用域分析抓不到，必须把公约全局名注册进来跨文件拦；
//   no-undef：杜绝意外全局泄漏与拼写错误。
// 其余规则保持 warn 不阻塞（1115 行存量代码，逐步消化）。
import globals from "globals";

export default [
  {
    files: ["src/web_static/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        ...globals.browser,
        // app.js 的跨文件全局（AGENT.md 公约点名的遮蔽高危名）：
        // t/tOr=i18n 翻译，chat/input/empty=全局 DOM 引用
        t: "readonly",
        tOr: "readonly",
        chat: "readonly",
        input: "readonly",
        empty: "readonly",
      },
    },
    rules: {
      "no-shadow": ["error", { builtinGlobals: true }],
      "no-undef": "error",
      "no-unused-vars": "warn",
    },
  },
];
