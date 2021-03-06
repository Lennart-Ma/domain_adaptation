from pyexpat import model
import torch
from torch import nn
import torch.nn.functional as F
from pytorch_lightning import LightningModule

from torchvision import models
from torchvision.models.segmentation.deeplabv3 import DeepLabHead

from models.UNet import UNET_ResNet18
from models.UNet import UNet

def createDeepLabv3():
    """DeepLabv3 class with custom head
    Returns:
        model: Returns the DeepLabv3 model with the ResNet101 backbone.
    """
    model = models.segmentation.deeplabv3_resnet101(pretrained=True,
                                                    progress=True,
                                                    )
    
    model.backbone.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3, bias=False)
    
    model.classifier = DeepLabHead(2048, num_classes=1) # num_classes is equal to output channels
    # Set the model in training mode
    # print(model)
    return model

class SegModel(LightningModule):
    """Semantic Segmentation Module.
    This is a basic semantic segmentation module implemented with Lightning.
    It uses CrossEntropyLoss as the default loss function. May be replaced with
    other loss functions as required.
    It is specific to KITTI dataset i.e. dataloaders are for KITTI
    and Normalize transform uses the mean and standard deviation of this dataset.
    It uses the FCN ResNet50 model as an example.
    Adam optimizer is used along with Cosine Annealing learning rate scheduler.
    SegModel(
      (net): UNet(
        (layers): ModuleList(
          (0): DoubleConv(...)
          (1): Down(...)
          (2): Down(...)
          (3): Up(...)
          (4): Up(...)
          (5): Conv2d(64, 19, kernel_size=(1, 1), stride=(1, 1))
        )
      )
    )
    """

    def __init__(
        self,
        model_name: str,
        seg_metric,
        num_classes: int = 1,
        lr: float = 1e-3,
        num_layers: int = 3,
        features_start: int = 64,
        bilinear: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.num_classes = num_classes
        self.lr = lr
        self.model_name = model_name
        self.num_layers = num_layers
        self.features_start = features_start
        self.bilinear = bilinear

        self.dice_coeff = seg_metric

        if model_name == "unet":
            self.net = UNet(
                num_classes=self.num_classes, num_layers=self.num_layers, features_start=self.features_start, bilinear=self.bilinear
            )
        
        if model_name == "unet_resnet_backbone":
            self.net = UNET_ResNet18(num_classes)

        elif model_name == "deeplabv3":
            self.net = createDeepLabv3()

        else:
            assert False, "No model selected"


        # save hyper-parameters to self.hparams (auto-logged by W&B)
        self.save_hyperparameters()

    def forward(self, x):

        if self.model_name == "deeplabv3":
            return self.net(x)['out']

        else:
            return self.net(x)

    def configure_optimizers(self):
        opt = torch.optim.Adam(self.net.parameters(), lr=self.lr)
        # sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=10)
        # return [opt], [sch]
        return opt

    def training_step(self, batch, batch_nb):
        img, mask = batch
        img = img.float()

        out = self(img)# needed when we use DeepLabv3 model   

        loss = F.binary_cross_entropy_with_logits(out, mask)
        log_dict = {"Binary Cross Entropy Loss": loss}

        # Log loss and metric
        self.log("Binary Cross Entropy Loss - Training", loss)

        return {"loss": loss, "log": log_dict, "progress_bar": log_dict}

    def validation_step(self, batch, batch_idx):
        
        batch_source = batch["loader_val_source"]
        batch_target = batch["loader_target_test"]

        img, mask = batch_source

        seg_out = self(img)

        loss_val = F.binary_cross_entropy_with_logits(seg_out, mask)
        dice_coeff_values = self.dice_coeff(seg_out, mask)
        curr_mean_dice = torch.mean(dice_coeff_values[:, -2], dim=0)

        # Log loss, metric and domain output
        self.log('Validation Loss - Source Data', loss_val, prog_bar=True, on_epoch=True)
        self.log('Dice Score - Source Data', curr_mean_dice, prog_bar=True,on_epoch=True)

        img, mask = batch_target

        seg_out = self(img)

        dice_coeff_values = self.dice_coeff(seg_out, mask)
        curr_mean_dice = torch.mean(dice_coeff_values[:, -2], dim=0) 

        self.log("Dice Score on Target Test Data", curr_mean_dice, on_epoch=True, prog_bar=True, sync_dist=True)

        return {"val_loss": loss_val}

    def test_step(self, batch, batch_idx):

        img, mask = batch

        seg_out = self(img)

        dice_coeff_values = self.dice_coeff(seg_out, mask)
        curr_mean_dice = torch.mean(dice_coeff_values[:, -2], dim=0)

        self.log("FINAL Dice Score on Target Test Data", curr_mean_dice, on_epoch=True, prog_bar=True, sync_dist=True)

        return {"curr_mean_dice": curr_mean_dice}