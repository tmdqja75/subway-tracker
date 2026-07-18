export const JOURNEY_STEPS = ["검색", "경로", "탑승", "이동", "완료"] as const;

export type JourneyStep = (typeof JOURNEY_STEPS)[number];

type JourneyStepperProps = {
  activeStep?: JourneyStep;
};

export function JourneyStepper({ activeStep = "검색" }: JourneyStepperProps) {
  return (
    <div className="journey-stepper">
      <ol aria-label="이동 단계" className="journey-stepper__steps">
        {JOURNEY_STEPS.map((step, index) => {
          const isActive = step === activeStep;

          return (
            <li
              aria-current={isActive ? "step" : undefined}
              data-state={isActive ? "active" : "inactive"}
              key={step}
            >
              <span aria-hidden="true" className="journey-stepper__number">
                {index + 1}
              </span>
              <span className="journey-stepper__label">{step}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
