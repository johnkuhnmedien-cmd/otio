import React from "react";
import { Composition } from "remotion";
import {
  defaultMapTransition,
  MapTransitionSchema,
  type MapTransitionProps,
} from "./schema";
import { VintageMapTransition } from "./VintageMapTransition";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="MapTransition"
      component={VintageMapTransition}
      durationInFrames={defaultMapTransition.durationInFrames}
      fps={defaultMapTransition.fps}
      width={3840}
      height={2160}
      defaultProps={defaultMapTransition}
      schema={MapTransitionSchema}
      calculateMetadata={({ props }: { props: MapTransitionProps }) => ({
        durationInFrames: props.durationInFrames,
        fps: props.fps,
        width: props.outputWidth,
        height: props.outputHeight,
      })}
    />
  );
};
