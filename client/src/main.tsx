import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import "antd/dist/reset.css";

import App from "./App";
import { ReviewPage } from "./pages/ReviewPage";
import "./styles.css";

// 不引入 router：`#review` 是内部审核页，C 端聊天保持 `/`。
const isReview = window.location.hash.replace(/^#\/?/, "") === "review";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#2563eb",
          borderRadius: 10,
        },
      }}
    >
      {isReview ? <ReviewPage /> : <App />}
    </ConfigProvider>
  </React.StrictMode>,
);
