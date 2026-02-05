// Type declarations for react-plotly.js
declare module "react-plotly.js" {
  import { Component } from "react";

  interface PlotParams {
    data: Array<Record<string, unknown>>;
    layout?: Record<string, unknown>;
    config?: Record<string, unknown>;
    style?: Record<string, string>;
    useResizeHandler?: boolean;
    onInitialized?: (figure: unknown, graphDiv: unknown) => void;
    onUpdate?: (figure: unknown, graphDiv: unknown) => void;
    onPurge?: (figure: unknown, graphDiv: unknown) => void;
    onError?: (err: unknown) => void;
  }

  class Plot extends Component<PlotParams> {}

  export default Plot;
}
