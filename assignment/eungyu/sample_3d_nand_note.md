# 3D NAND Flash Study Note

3D NAND Flash는 memory cell을 수직 방향으로 적층하여
평면 NAND Flash의 scaling 한계를 완화하기 위한 메모리 구조이다.

## 구조

3D NAND에서는 여러 층의 Word Line이 수직으로 적층되어 있으며,
Vertical Channel이 이 층들을 관통한다.

주요 구성 요소는 다음과 같다.

- Vertical Channel
- Word Line
- Gate Dielectric
- Charge Trap Layer
- Bit Line
- Source Line

## 동작

Program 동작에서는 전자를 Charge Trap Layer에 저장하여
cell의 threshold voltage를 변화시킨다.

Erase 동작에서는 저장된 전자를 제거하여
threshold voltage를 다시 낮춘다.

Read 동작에서는 선택된 Word Line에 적절한 전압을 인가하고
channel current를 측정하여 저장 상태를 판단한다.

## 주요 이슈

3D NAND의 주요 기술적 이슈는 다음과 같다.

- 적층 수 증가에 따른 공정 난이도 증가
- Channel Hole의 높은 aspect ratio
- Cell-to-cell variation
- Retention
- Program/Erase endurance